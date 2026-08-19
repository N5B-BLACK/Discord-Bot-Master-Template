"""
Paddle billing (Phase 5) - creates Transactions for the Pro subscription and
processes Paddle's webhook events to keep each guild's license in sync with
what they're actually paying for, automatically.

Paddle's checkout works differently from Stripe's: there's no Paddle-hosted
checkout domain to redirect to. Instead:
1. We create a Transaction via Paddle's REST API (server-side, this file).
2. We send the browser to our OWN upgrade page with ?_ptxn=<transaction_id>.
3. That page embeds Paddle.js, which detects _ptxn and opens the checkout
   overlay for that transaction right there (see dashboard_pages.upgrade_page
   and its UPGRADE_JS for the client side of this).
4. The actual license activation happens via the webhook below, not the
   client-side checkout completion - the browser tab closing early shouldn't
   mean a paying customer never gets their Pro features.

No official Paddle SDK exists for Python (only Node.js and PHP), so webhook
signature verification is implemented by hand here, following Paddle's
documented algorithm exactly: https://developer.paddle.com/webhooks/about/signature-verification/
"""

import hashlib
import hmac as hmac_module
import logging
import time

import aiohttp

import config
from utils.db import (
    get_guild_by_paddle_subscription,
    set_license,
    set_paddle_ids,
    set_payment_issue,
)

logger = logging.getLogger("bot")

_API_BASE = "https://sandbox-api.paddle.com" if config.PADDLE_ENVIRONMENT == "sandbox" else "https://api.paddle.com"


def is_configured() -> bool:
    return bool(config.PADDLE_API_KEY and config.PADDLE_PRICE_ID_PRO and config.PADDLE_WEBHOOK_SECRET)


async def create_transaction(guild_id: int, guild_name: str) -> str:
    """Creates a Paddle Transaction for the Pro price and returns its ID, ready
    to hand to Paddle.js on the client (Paddle.Checkout.open({transactionId}))."""
    headers = {"Authorization": f"Bearer {config.PADDLE_API_KEY}", "Content-Type": "application/json"}
    body = {
        "items": [{"price_id": config.PADDLE_PRICE_ID_PRO, "quantity": 1}],
        "custom_data": {"guild_id": str(guild_id), "guild_name": guild_name},
    }
    async with aiohttp.ClientSession() as http:
        async with http.post(f"{_API_BASE}/transactions", json=body, headers=headers) as resp:
            data = await resp.json()
            if resp.status >= 400:
                raise PaddleAPIError(data.get("error", {}).get("detail", f"HTTP {resp.status}"))
            return data["data"]["id"]


async def create_customer_portal_session(customer_id: str) -> str:
    """Paddle's equivalent of Stripe's Billing Portal - a hosted page where the
    customer can update payment details or cancel. Returns the portal URL."""
    headers = {"Authorization": f"Bearer {config.PADDLE_API_KEY}"}
    async with aiohttp.ClientSession() as http:
        async with http.get(f"{_API_BASE}/customers/{customer_id}/portal-sessions", headers=headers) as resp:
            data = await resp.json()
            if resp.status >= 400:
                raise PaddleAPIError(data.get("error", {}).get("detail", f"HTTP {resp.status}"))
            # general_url covers the customer's full self-serve portal (all subscriptions);
            # falls back gracefully if Paddle's response shape doesn't include it.
            urls = data["data"].get("urls", {})
            return urls.get("general", {}).get("overview") or urls.get("general")


class PaddleAPIError(Exception):
    pass


def verify_signature(raw_body: bytes, signature_header: str, secret: str, tolerance_seconds: int = 300) -> bool:
    """Implements Paddle's documented HMAC-SHA256 verification exactly:
    header format 'ts=<unix_ts>;h1=<hex>', signed payload is '<ts>:<raw_body>'.
    Also rejects stale timestamps (replay protection) - 5 minute tolerance
    comfortably covers real network/processing delay without being so loose
    it defeats the point of checking the timestamp at all."""
    if not signature_header:
        return False

    parts = dict(p.split("=", 1) for p in signature_header.split(";") if "=" in p)
    ts = parts.get("ts")
    h1 = parts.get("h1")
    if not ts or not h1:
        return False

    try:
        if abs(time.time() - int(ts)) > tolerance_seconds:
            return False
    except ValueError:
        return False

    signed_payload = f"{ts}:".encode() + raw_body
    expected = hmac_module.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return hmac_module.compare_digest(expected, h1)


async def handle_event(event: dict) -> None:
    event_type = event.get("event_type")
    data = event.get("data", {})

    if event_type == "transaction.completed":
        custom_data = data.get("custom_data") or {}
        guild_id = _extract_guild_id(custom_data)
        if guild_id is None:
            logger.error(f"Paddle transaction.completed with no guild_id in custom_data: {data.get('id')}")
            return
        await set_license(guild_id, "pro", None)
        await set_paddle_ids(guild_id, data.get("customer_id"), data.get("subscription_id"))
        await set_payment_issue(guild_id, False)
        logger.info(f"Guild {guild_id} upgraded to Pro via Paddle transaction {data.get('id')}")

    elif event_type == "subscription.past_due":
        subscription_id = data.get("id")
        if subscription_id:
            doc = await get_guild_by_paddle_subscription(subscription_id)
            if doc:
                await set_payment_issue(doc["guild_id"], True)
                logger.warning(f"Guild {doc['guild_id']}: Paddle subscription past due, marked payment_issue")

    elif event_type == "subscription.activated":
        subscription_id = data.get("id")
        if subscription_id:
            doc = await get_guild_by_paddle_subscription(subscription_id)
            if doc:
                await set_payment_issue(doc["guild_id"], False)

    elif event_type == "subscription.canceled":
        subscription_id = data.get("id")
        doc = await get_guild_by_paddle_subscription(subscription_id)
        if doc:
            await set_license(doc["guild_id"], "free", None)
            await set_payment_issue(doc["guild_id"], False)
            logger.info(f"Guild {doc['guild_id']}: Paddle subscription ended, downgraded to Free")


def _extract_guild_id(custom_data: dict) -> int | None:
    raw = custom_data.get("guild_id") if custom_data else None
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None
