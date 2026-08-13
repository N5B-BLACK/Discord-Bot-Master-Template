"""
Dashboard - JSON API routes (Phase 0 refactor, split from the former single
dashboard.py).

Every function here is a POST/DELETE handler behind a "Save" button on a
settings page - always guild-scoped, always re-verifies Discord permissions
server-side via _guarded_guild (never trusts the guild_id in the URL alone).
Page/HTML routes live in dashboard_pages.py instead - see there for those.
"""

import discord
from aiohttp import web

from dashboard_core import LOG_COLOR_KEYS, SETTINGS_LABELS, VALID_KEYS, _guarded_guild, _read_session
from utils.db import (
    add_auto_divider_channel,
    delete_embed_draft,
    get_embed_draft,
    list_embed_drafts,
    remove_auto_divider_channel,
    save_embed_draft,
    set_auto_divider_enabled,
    set_auto_divider_image,
    set_log_color,
    update_guild_setting,
)
from utils.embed_builder import EmbedValidationError, blank_embed_json, to_discord_embed
from utils.log_helper import log_setting_change
from utils.message_templates import TEMPLATE_SLOT_KEYS

# ---------------------------------------------------------
# API routes (JSON) - all guild-scoped, all re-verify Discord permissions server-side
# ---------------------------------------------------------
async def save_guild_setting(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard
    guild = guard

    body = await request.json()
    key = body.get("key")
    value = body.get("value")
    if key not in VALID_KEYS:
        return web.json_response({"error": "invalid setting key"}, status=400)

    parsed_value = int(value) if value else None
    await update_guild_setting(guild_id, key, parsed_value)

    if parsed_value:
        target = guild.get_channel(parsed_value) or guild.get_role(parsed_value)
        display_value = target.mention if target else f"`{parsed_value}`"
    else:
        display_value = "*Not set*"
    session_data = _read_session(request)
    username = session_data.get("user", {}).get("username", "someone")
    await log_setting_change(guild, SETTINGS_LABELS.get(key, key), display_value, f"{username} (dashboard)")

    return web.json_response({"ok": True})


async def save_branding(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    color_hex = body.get("embed_color")
    if color_hex:
        try:
            color_int = int(color_hex.lstrip("#"), 16)
        except ValueError:
            return web.json_response({"error": "invalid color"}, status=400)
        await update_guild_setting(guild_id, "embed_color", color_int)

    for key in ("embed_icon_url", "embed_footer_text", "embed_footer_icon_url"):
        await update_guild_setting(guild_id, key, body.get(key) or None)

    return web.json_response({"ok": True})


async def save_log_color(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    key = body.get("key")
    if key not in LOG_COLOR_KEYS:
        return web.json_response({"error": "invalid log type"}, status=400)

    color_hex = body.get("color")
    color_int = None
    if color_hex:
        try:
            color_int = int(color_hex.lstrip("#"), 16)
        except ValueError:
            return web.json_response({"error": "invalid color"}, status=400)

    await set_log_color(guild_id, key, color_int)
    return web.json_response({"ok": True})


async def save_template_slot(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    slot = body.get("slot")
    if slot not in TEMPLATE_SLOT_KEYS:
        return web.json_response({"error": "invalid template slot"}, status=400)

    value = (body.get("value") or "").strip() or None
    if value:
        draft = await get_embed_draft(guild_id, value)
        if not draft:
            return web.json_response({"error": "that draft no longer exists"}, status=400)

    await update_guild_setting(guild_id, slot, value)
    return web.json_response({"ok": True})


async def save_divider_enabled(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    await set_auto_divider_enabled(guild_id, bool(body.get("enabled")))
    return web.json_response({"ok": True})


async def save_divider_image(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    image_url = (body.get("image_url") or "").strip() or None
    await set_auto_divider_image(guild_id, image_url)
    return web.json_response({"ok": True})


async def add_divider_channel_route(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    try:
        channel_id = int(body.get("channel_id"))
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid channel"}, status=400)

    await add_auto_divider_channel(guild_id, channel_id)
    return web.json_response({"ok": True})


async def remove_divider_channel_route(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    try:
        channel_id = int(request.match_info["channel_id"])
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid channel"}, status=400)

    await remove_auto_divider_channel(guild_id, channel_id)
    return web.json_response({"ok": True})


async def post_ticket_panel(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard
    guild = guard

    body = await request.json()
    try:
        channel_id = int(body.get("channel_id"))
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid channel"}, status=400)

    channel = guild.get_channel(channel_id)
    if channel is None:
        return web.json_response({"error": "channel not found"}, status=404)

    # imported here (not at module level) to avoid a circular import between
    # dashboard.py and cogs.tickets
    from cogs.tickets import TicketPanelView, build_panel_embed

    embed = await build_panel_embed(guild_id, guild.name)
    try:
        await channel.send(embed=embed, view=TicketPanelView())
    except discord.Forbidden:
        return web.json_response({"error": "bot lacks permission to post in that channel"}, status=403)

    return web.json_response({"ok": True})


async def embeds_list(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    drafts = await list_embed_drafts(guild_id)
    return web.json_response({"drafts": [d["name"] for d in drafts]})


async def embeds_get_one(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    name = request.match_info["name"]
    draft = await get_embed_draft(guild_id, name)
    if not draft:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"name": draft["name"], "embed_json": draft["embed_json"]})


async def embeds_delete_one(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    name = request.match_info["name"]
    await delete_embed_draft(guild_id, name)
    return web.json_response({"ok": True})


async def embeds_save(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    name = (body.get("name") or "").strip()[:80]
    embed_json = body.get("embed_json") or blank_embed_json()
    if not name:
        return web.json_response({"error": "draft needs a name"}, status=400)

    try:
        to_discord_embed(embed_json)  # validates without sending
    except EmbedValidationError as e:
        return web.json_response({"error": str(e)}, status=400)

    session_data = _read_session(request)
    user_id = int(session_data.get("user", {}).get("id", 0))
    await save_embed_draft(guild_id, name, embed_json, user_id)
    return web.json_response({"ok": True})


async def embeds_send(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard
    guild = guard

    body = await request.json()
    embed_json = body.get("embed_json") or {}
    try:
        channel_id = int(body.get("channel_id"))
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid channel"}, status=400)

    channel = guild.get_channel(channel_id)
    if channel is None:
        return web.json_response({"error": "channel not found"}, status=404)

    try:
        embed = to_discord_embed(embed_json)
    except EmbedValidationError as e:
        return web.json_response({"error": str(e)}, status=400)

    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        return web.json_response({"error": "bot lacks permission to post in that channel"}, status=403)

    return web.json_response({"ok": True})


