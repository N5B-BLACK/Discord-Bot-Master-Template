"""
Licensing (Phase 5) - two-tier subscription enforcement: Free and Pro, on one
shared bot serving every guild. A guild's plan lives in its settings
(utils/db.py's `license` block) and gates any module utils/module_registry.py
marks tier="pro" - everything else (tier="core") is free and unrestricted.

Every guild defaults to "free" (see utils/db.py's DEFAULT_SETTINGS). "unlimited"
is a manual override for internal/test servers - it always passes every check,
set via /license set (cogs/license_admin.py, bot-owner only).
"""

import datetime

_TIER_RANK = {"free": 0, "pro": 1}
_PLAN_RANK = {"free": 0, "pro": 1, "unlimited": 99}


def is_license_active(settings: dict) -> bool:
    """False only if a plan has an expiry date that's passed. No expiry = always active."""
    license_info = settings.get("license", {})
    expires_at = license_info.get("expires_at")
    if not expires_at:
        return True
    try:
        expiry = datetime.datetime.fromisoformat(expires_at)
    except ValueError:
        return True  # malformed date saved somehow - fail open rather than lock someone out by accident
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc) < expiry


def is_module_available(settings: dict, module_key: str) -> bool:
    """Whether a guild's current plan covers a given module's tier. Modules
    with tier="core" (the default for everything today) are always available
    regardless of plan - only "pro"-tiered modules are actually gated; "core"
    (the default) always passes."""
    from utils.module_registry import get_module  # local import - avoids a cross-module import cycle

    mod = get_module(module_key)
    if mod is None or mod.tier not in _TIER_RANK:
        return True

    if not is_license_active(settings):
        return False

    plan = settings.get("license", {}).get("plan", "unlimited")
    plan_rank = _PLAN_RANK.get(plan, _PLAN_RANK["unlimited"])
    return plan_rank >= _TIER_RANK[mod.tier]
