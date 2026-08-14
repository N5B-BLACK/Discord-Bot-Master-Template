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
    add_banned_word,
    add_leveling_ignored_channel,
    add_link_whitelist_channel,
    add_link_whitelist_domain,
    add_security_whitelist_user,
    delete_embed_draft,
    get_embed_draft,
    list_embed_drafts,
    remove_auto_divider_channel,
    remove_banned_word,
    remove_leveling_ignored_channel,
    remove_link_whitelist_channel,
    remove_link_whitelist_domain,
    remove_security_whitelist_user,
    save_embed_draft,
    set_auto_divider_enabled,
    set_auto_divider_image,
    set_level_role,
    set_leveling_setting,
    set_log_color,
    set_security_setting,
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


# ---------------------------------------------------------
# Security Suite (Phase 1) - see cogs/security.py for the enforcement side.
# ---------------------------------------------------------
_SECURITY_SYSTEMS = {"anti_nuke", "anti_spam", "anti_link", "word_filter", "anti_webhook", "raid_mode"}


async def save_security_toggle(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    system = body.get("system")
    if system not in _SECURITY_SYSTEMS:
        return web.json_response({"error": "invalid system"}, status=400)
    await set_security_setting(guild_id, f"{system}.enabled", bool(body.get("enabled")))
    return web.json_response({"ok": True})


async def save_security_log_channel(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    raw = body.get("channel_id")
    channel_id = int(raw) if raw else None
    await set_security_setting(guild_id, "log_channel_id", channel_id)
    return web.json_response({"ok": True})


async def save_anti_nuke_config(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    try:
        threshold = max(1, int(body.get("action_threshold", 3)))
        window = max(1, int(body.get("window_seconds", 10)))
    except (TypeError, ValueError):
        return web.json_response({"error": "threshold/window must be numbers"}, status=400)
    punishment = body.get("punishment") if body.get("punishment") in ("strip_roles", "ban") else "strip_roles"

    await set_security_setting(guild_id, "anti_nuke.action_threshold", threshold)
    await set_security_setting(guild_id, "anti_nuke.window_seconds", window)
    await set_security_setting(guild_id, "anti_nuke.punishment", punishment)
    return web.json_response({"ok": True})


async def save_anti_spam_config(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    try:
        threshold = max(1, int(body.get("message_threshold", 6)))
        window = max(1, int(body.get("window_seconds", 7)))
        timeout_seconds = max(1, int(body.get("timeout_seconds", 300)))
    except (TypeError, ValueError):
        return web.json_response({"error": "threshold/window/timeout must be numbers"}, status=400)

    await set_security_setting(guild_id, "anti_spam.message_threshold", threshold)
    await set_security_setting(guild_id, "anti_spam.window_seconds", window)
    await set_security_setting(guild_id, "anti_spam.timeout_seconds", timeout_seconds)
    return web.json_response({"ok": True})


async def save_security_whitelist_user(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    try:
        user_id = int(body.get("user_id"))
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid user id"}, status=400)

    if body.get("remove"):
        await remove_security_whitelist_user(guild_id, user_id)
    else:
        await add_security_whitelist_user(guild_id, user_id)
    return web.json_response({"ok": True})


async def save_banned_word(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    word = (body.get("word") or "").strip()
    if not word:
        return web.json_response({"error": "word required"}, status=400)

    if body.get("remove"):
        await remove_banned_word(guild_id, word)
    else:
        await add_banned_word(guild_id, word)
    return web.json_response({"ok": True})


async def save_link_whitelist_domain(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    domain = (body.get("domain") or "").strip().lower().removeprefix("www.")
    if not domain:
        return web.json_response({"error": "domain required"}, status=400)

    if body.get("remove"):
        await remove_link_whitelist_domain(guild_id, domain)
    else:
        await add_link_whitelist_domain(guild_id, domain)
    return web.json_response({"ok": True})


async def save_link_whitelist_channel(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    try:
        channel_id = int(body.get("channel_id"))
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid channel"}, status=400)

    if body.get("remove"):
        await remove_link_whitelist_channel(guild_id, channel_id)
    else:
        await add_link_whitelist_channel(guild_id, channel_id)
    return web.json_response({"ok": True})


async def save_anti_webhook_config(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    punishment = body.get("punishment") if body.get("punishment") in ("strip_roles", "ban") else "strip_roles"
    await set_security_setting(guild_id, "anti_webhook.punishment", punishment)
    return web.json_response({"ok": True})


async def save_raid_mode_config(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    try:
        join_threshold = max(1, int(body.get("join_threshold", 5)))
        window = max(1, int(body.get("window_seconds", 10)))
        min_age = max(0, int(body.get("min_account_age_hours", 24)))
        duration = max(1, int(body.get("lockdown_duration_minutes", 15)))
    except (TypeError, ValueError):
        return web.json_response({"error": "numeric fields must be numbers"}, status=400)
    action = body.get("action") if body.get("action") in ("lockdown", "kick_new_accounts") else "lockdown"

    await set_security_setting(guild_id, "raid_mode.join_threshold", join_threshold)
    await set_security_setting(guild_id, "raid_mode.window_seconds", window)
    await set_security_setting(guild_id, "raid_mode.action", action)
    await set_security_setting(guild_id, "raid_mode.min_account_age_hours", min_age)
    await set_security_setting(guild_id, "raid_mode.lockdown_duration_minutes", duration)
    return web.json_response({"ok": True})


# ---------------------------------------------------------
# Leveling (Phase 2) - see cogs/leveling.py for XP awarding + rank cards.
# ---------------------------------------------------------
async def save_leveling_toggle(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    await set_leveling_setting(guild_id, "enabled", bool(body.get("enabled")))
    return web.json_response({"ok": True})


async def save_leveling_config(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    try:
        xp_min = max(1, int(body.get("xp_min", 15)))
        xp_max = max(1, int(body.get("xp_max", 25)))
        cooldown = max(1, int(body.get("cooldown_seconds", 60)))
    except (TypeError, ValueError):
        return web.json_response({"error": "xp_min/xp_max/cooldown must be numbers"}, status=400)
    if xp_min > xp_max:
        xp_min, xp_max = xp_max, xp_min

    raw_channel = body.get("announce_channel_id")
    announce_channel_id = int(raw_channel) if raw_channel else None
    announce_message = (body.get("announce_message") or "").strip() or None

    await set_leveling_setting(guild_id, "xp_min", xp_min)
    await set_leveling_setting(guild_id, "xp_max", xp_max)
    await set_leveling_setting(guild_id, "cooldown_seconds", cooldown)
    await set_leveling_setting(guild_id, "announce_channel_id", announce_channel_id)
    if announce_message:
        await set_leveling_setting(guild_id, "announce_message", announce_message)
    return web.json_response({"ok": True})


async def save_leveling_ignored_channel(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    try:
        channel_id = int(body.get("channel_id"))
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid channel"}, status=400)

    if body.get("remove"):
        await remove_leveling_ignored_channel(guild_id, channel_id)
    else:
        await add_leveling_ignored_channel(guild_id, channel_id)
    return web.json_response({"ok": True})


async def save_level_role(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    try:
        level = int(body.get("level"))
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid level"}, status=400)

    if body.get("remove"):
        await set_level_role(guild_id, level, None)
    else:
        try:
            role_id = int(body.get("role_id"))
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid role"}, status=400)
        await set_level_role(guild_id, level, role_id)
    return web.json_response({"ok": True})
