"""
MongoDB access layer - stores each server's (guild) settings separately.
Each guild has its own document, keyed by guild_id.
"""

import datetime

import motor.motor_asyncio
from pymongo import ReturnDocument

import config
from utils.module_registry import default_enabled_modules

_client = motor.motor_asyncio.AsyncIOMotorClient(config.MONGODB_URI)
_db = _client["discord_bot"]
_guild_settings = _db["guild_settings"]
_tickets = _db["tickets"]
_counters = _db["ticket_counters"]
_warnings = _db["warnings"]
_embed_drafts = _db["embed_drafts"]
_user_levels = _db["user_levels"]
_reaction_roles = _db["reaction_roles"]
_voice_rooms = _db["voice_rooms"]
_guild_stats_daily = _db["guild_stats_daily"]
_event_logs = _db["event_logs"]


async def check_connection() -> bool:
    """Simple ping to the database - returns True if connected, raises if there's a problem."""
    await _client.admin.command("ping")
    return True


# Default values - returned when a server has no saved setting (i.e. no restriction)
DEFAULT_SETTINGS = {
    "mod_role_id": None,
    "warn_log_channel_id": None,
    "welcome_channel_id": None,
    "auto_role_id": None,
    "bot_auto_role_id": None,
    "ai_chat_channel_id": None,
    "ticket_support_role_id": None,
    "ticket_log_channel_id": None,
    "voice_join_leave_log_channel_id": None,
    "voice_switch_log_channel_id": None,
    "voice_disconnect_log_channel_id": None,
    "voice_mute_log_channel_id": None,
    "voice_deafen_log_channel_id": None,
    "ban_unban_log_channel_id": None,
    "server_join_leave_log_channel_id": None,
    "msg_deleted_log_channel_id": None,
    "timeout_log_channel_id": None,
    "kicked_log_channel_id": None,
    "setup_update_log_channel_id": None,
    "message_edit_log_channel_id": None,
    "message_bulk_delete_log_channel_id": None,
    "channel_create_log_channel_id": None,
    "channel_delete_log_channel_id": None,
    "channel_update_log_channel_id": None,
    "role_create_log_channel_id": None,
    "role_delete_log_channel_id": None,
    "role_update_log_channel_id": None,
    "nickname_change_log_channel_id": None,
    "member_role_change_log_channel_id": None,
    "thread_create_log_channel_id": None,
    "thread_delete_log_channel_id": None,
    "thread_update_log_channel_id": None,
    "voice_move_log_channel_id": None,
    "trap_channel_id": None,
    "embed_color": None,
    "embed_icon_url": None,
    "embed_footer_text": None,
    "embed_footer_icon_url": None,
    "log_colors": {},
    "welcome_embed_template": None,
    "ticket_panel_embed_template": None,
    "ticket_open_embed_template": None,
    "auto_divider": {"enabled": False, "image_url": None, "channel_ids": []},
    # Phase 1 (Security Suite) - each sub-system is independently toggleable.
    # anti_nuke tracks destructive actions (channel/role delete) per actor via the
    # audit log; anti_spam tracks message rate per member; anti_link/word_filter
    # scan message content. All four log to `log_channel_id` if set, otherwise to
    # `warn_log_channel_id` as a fallback so security alerts are never silently lost.
    "security": {
        "log_channel_id": None,
        "whitelist_user_ids": [],  # exempt from anti-nuke punishment (e.g. co-owners, other bots)
        "anti_nuke": {
            "enabled": False,
            "action_threshold": 3,      # destructive actions...
            "window_seconds": 10,       # ...within this many seconds triggers punishment
            "punishment": "strip_roles",  # "strip_roles" or "ban"
        },
        "anti_spam": {
            "enabled": False,
            "message_threshold": 6,     # messages...
            "window_seconds": 7,        # ...within this many seconds triggers a timeout
            "timeout_seconds": 300,
        },
        "anti_link": {
            "enabled": False,
            "whitelist_domains": [],
            "whitelist_channel_ids": [],
        },
        "word_filter": {
            "enabled": False,
            "banned_words": [],
        },
        "anti_webhook": {
            "enabled": False,
            "punishment": "strip_roles",  # "strip_roles" or "ban" - same options as anti_nuke
        },
        "raid_mode": {
            "enabled": False,
            "join_threshold": 5,          # this many joins...
            "window_seconds": 10,         # ...within this many seconds triggers a raid response
            "action": "lockdown",         # "lockdown" (raise verification level temporarily) or "kick_new_accounts"
            "min_account_age_hours": 24,  # kick_new_accounts mode: only kicks accounts younger than this
            "lockdown_duration_minutes": 15,  # lockdown mode: auto-reverts verification level after this long
        },
    },
    # Phase 2 (Engagement) - leveling. Per-user XP itself lives in its own
    # `user_levels` collection (see below), not here - this block is just the
    # per-guild *configuration* of the system.
    "leveling": {
        "enabled": False,
        "xp_min": 15,             # XP awarded per eligible message is random between...
        "xp_max": 25,             # ...these two values (keeps the curve from feeling robotic)
        "cooldown_seconds": 60,   # a member can only earn XP once per this many seconds
        "announce_channel_id": None,  # None = announce in the channel the level-up happened in
        "announce_message": "🎉 {member_mention} leveled up to **level {level}**!",
        "level_roles": {},        # {"5": role_id, "10": role_id, ...} - awarded automatically on reaching that level
        "ignored_channel_ids": [],
    },
    # Phase 3 (Community) - private voice rooms. Active rooms themselves live in
    # their own `voice_rooms` collection (one doc per temp channel); this block
    # is just the per-guild *configuration* of the "join to create" system.
    "voice_rooms": {
        "enabled": False,
        "hub_channel_id": None,       # the voice channel members join to trigger room creation
        "category_id": None,          # where new rooms are created; None = same category as the hub
        "name_template": "{username}'s Room",
        "default_user_limit": 0,      # 0 = unlimited
    },
    # Phase 4 (White-label) - lets whoever owns this guild's bot deployment
    # (e.g. Ali's client) rebrand the dashboard chrome itself - the sidebar
    # name/logo and the accent color - without touching any code. Distinct
    # from embed_color/embed_icon_url above, which only affect the bot's own
    # Discord embeds, not the web dashboard's appearance.
    "dashboard_branding": {
        "product_name": None,   # None = falls back to "Bot Dashboard"
        "logo_url": None,       # None = falls back to the guild's own icon
        "accent_hex": None,     # None = falls back to the default amber accent
    },
    # Phase 5 (business layer) - one bot, two-tier subscription (Free/Pro)
    # inside it. New guilds start on "free"; Pro is granted manually via
    # /license set until a payment flow exists. IMPORTANT: this defaults every
    # NEW guild to "free" tier, which restricts anything module_registry.py
    # marks tier="pro" (security suite, voice rooms, music) - grant your own
    # test/dev servers "unlimited" after deploying.
    "license": {
        "plan": "free",       # "free" | "pro" | "unlimited" (unlimited always passes every check)
        "expires_at": None,   # ISO string; None = never expires
        "paddle_customer_id": None,
        "paddle_subscription_id": None,
        "payment_issue": False,  # True while Paddle is retrying a failed payment (subscription still active, shown as a warning)
    },
    # Phase 0 addition (Module Registry) - which whole feature modules are on/off
    # per guild. Seeded from utils/module_registry.py so adding a new planned
    # module there automatically appears here with its default state.
    "enabled_modules": default_enabled_modules(),
}


async def get_guild_settings(guild_id: int) -> dict:
    """Returns the saved settings for a server, or defaults if the admin hasn't configured anything yet."""
    import copy

    doc = await _guild_settings.find_one({"guild_id": guild_id})
    # deepcopy, not copy() - DEFAULT_SETTINGS contains a mutable dict (log_colors: {}); a shallow
    # copy would let every guild's "default" settings share and potentially corrupt the same dict.
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    if doc:
        settings.update({k: v for k, v in doc.items() if k in DEFAULT_SETTINGS})
    return settings


async def update_guild_setting(guild_id: int, key: str, value) -> None:
    """Updates a single setting for a server (creates the document on first use)."""
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$set": {key: value, "guild_id": guild_id}},
        upsert=True,
    )


async def set_log_color(guild_id: int, setting_key: str, color_int) -> None:
    """Sets (or clears, if color_int is None) a per-log-type embed color override -
    e.g. make the ban/unban log always red regardless of the server's brand color."""
    if color_int is None:
        await _guild_settings.update_one(
            {"guild_id": guild_id},
            {"$unset": {f"log_colors.{setting_key}": ""}, "$set": {"guild_id": guild_id}},
            upsert=True,
        )
    else:
        await _guild_settings.update_one(
            {"guild_id": guild_id},
            {"$set": {f"log_colors.{setting_key}": color_int, "guild_id": guild_id}},
            upsert=True,
        )


async def set_license(guild_id: int, plan: str, expires_at: str = None) -> None:
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$set": {"license.plan": plan, "license.expires_at": expires_at, "guild_id": guild_id}},
        upsert=True,
    )


async def set_paddle_ids(guild_id: int, customer_id: str, subscription_id: str) -> None:
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$set": {
            "license.paddle_customer_id": customer_id,
            "license.paddle_subscription_id": subscription_id,
            "guild_id": guild_id,
        }},
        upsert=True,
    )


async def set_payment_issue(guild_id: int, has_issue: bool) -> None:
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$set": {"license.payment_issue": has_issue, "guild_id": guild_id}},
    )


async def get_guild_by_paddle_subscription(subscription_id: str) -> dict | None:
    """Used by the Paddle webhook handler - a subscription lifecycle event only
    tells us the subscription ID, not the guild, so this is the reverse lookup."""
    return await _guild_settings.find_one({"license.paddle_subscription_id": subscription_id})


async def list_licensed_guilds() -> list:
    """Every guild with a non-default license state (plan != free, has Paddle
    IDs, or has a payment issue) - powers the owner-only /admin page. Guilds
    that have never touched billing at all aren't worth listing."""
    cursor = _guild_settings.find({
        "$or": [
            {"license.plan": {"$in": ["pro", "unlimited"]}},
            {"license.paddle_customer_id": {"$ne": None}},
            {"license.payment_issue": True},
        ]
    })
    return await cursor.to_list(length=1000)


async def set_module_enabled(guild_id: int, module_key: str, enabled: bool) -> None:
    """Toggles a whole feature module on/off for a guild (Module Registry, Phase 0).
    Does not touch the module's individual settings - just whether it's active."""
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$set": {f"enabled_modules.{module_key}": enabled, "guild_id": guild_id}},
        upsert=True,
    )


async def create_ticket(guild_id: int, thread_id: int, opener_id: int) -> None:
    """Registers a new ticket - who opened it, not claimed yet."""
    await _tickets.update_one(
        {"thread_id": thread_id},
        {"$set": {"guild_id": guild_id, "opener_id": opener_id, "claimed_by": None}},
        upsert=True,
    )


async def get_ticket(thread_id: int):
    """Returns ticket data (guild_id, opener_id, claimed_by), or None if not found."""
    return await _tickets.find_one({"thread_id": thread_id})


async def set_ticket_claim(thread_id: int, staff_id: int) -> None:
    """Records who claimed the ticket."""
    await _tickets.update_one({"thread_id": thread_id}, {"$set": {"claimed_by": staff_id}})


async def get_next_ticket_number(guild_id: int) -> int:
    """Returns a new sequential ticket number (starts at 1 per server) - atomic, so no duplicates."""
    doc = await _counters.find_one_and_update(
        {"guild_id": guild_id},
        {"$inc": {"count": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc["count"]


async def mark_ticket_closed(thread_id: int, delete_at) -> None:
    """Records ticket close time and the scheduled auto-deletion time (24h later)."""
    await _tickets.update_one(
        {"thread_id": thread_id},
        {"$set": {"delete_at": delete_at, "deleted": False}},
    )


async def get_tickets_due_for_deletion(now) -> list:
    """Returns all closed tickets whose scheduled deletion time has passed and aren't deleted yet."""
    cursor = _tickets.find({"delete_at": {"$lte": now}, "deleted": {"$ne": True}})
    return await cursor.to_list(length=100)


async def mark_ticket_deleted(thread_id: int) -> None:
    await _tickets.update_one({"thread_id": thread_id}, {"$set": {"deleted": True}})


async def add_warning(guild_id: int, member_id: int, reason: str, by: int) -> list:
    """Adds a warning entry for a member and returns their updated warning list."""
    import datetime

    entry = {"reason": reason or "Not specified", "by": by, "date": str(datetime.date.today())}
    doc = await _warnings.find_one_and_update(
        {"guild_id": guild_id, "member_id": member_id},
        {"$push": {"entries": entry}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc["entries"]


async def get_warnings(guild_id: int, member_id: int) -> list:
    doc = await _warnings.find_one({"guild_id": guild_id, "member_id": member_id})
    return doc["entries"] if doc else []


# ---------------------------------------------------------
# Embed Builder - full custom-embed drafts (title/description/color/author/
# footer/image/thumbnail/fields/timestamp), saved per guild so they can be
# built in the dashboard, then sent from Discord or the dashboard itself.
# ---------------------------------------------------------
async def save_embed_draft(guild_id: int, name: str, embed_json: dict, author_id: int) -> None:
    """Creates or overwrites a saved embed draft (unique per guild by name)."""
    import datetime

    await _embed_drafts.update_one(
        {"guild_id": guild_id, "name": name},
        {
            "$set": {
                "embed_json": embed_json,
                "updated_by": author_id,
                "updated_at": datetime.datetime.utcnow(),
            }
        },
        upsert=True,
    )


async def list_embed_drafts(guild_id: int) -> list:
    cursor = _embed_drafts.find({"guild_id": guild_id}).sort("updated_at", -1)
    return await cursor.to_list(length=100)


async def get_embed_draft(guild_id: int, name: str):
    return await _embed_drafts.find_one({"guild_id": guild_id, "name": name})


async def delete_embed_draft(guild_id: int, name: str) -> None:
    await _embed_drafts.delete_one({"guild_id": guild_id, "name": name})


# ---------------------------------------------------------
# Auto Divider - posts a configured image after every message in chosen channels.
# Stored as one nested doc (not flat settings keys) since it has a list field
# (channel_ids) that needs atomic add/remove operations.
# ---------------------------------------------------------
async def set_auto_divider_image(guild_id: int, image_url: str | None) -> None:
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$set": {"auto_divider.image_url": image_url, "guild_id": guild_id}},
        upsert=True,
    )


async def set_auto_divider_enabled(guild_id: int, enabled: bool) -> None:
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$set": {"auto_divider.enabled": enabled, "guild_id": guild_id}},
        upsert=True,
    )


async def add_auto_divider_channel(guild_id: int, channel_id: int) -> None:
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$addToSet": {"auto_divider.channel_ids": channel_id}, "$set": {"guild_id": guild_id}},
        upsert=True,
    )


async def remove_auto_divider_channel(guild_id: int, channel_id: int) -> None:
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$pull": {"auto_divider.channel_ids": channel_id}},
    )


# ---------------------------------------------------------
# Security Suite (Phase 1) - anti-nuke / anti-spam / anti-link / word filter.
# Same nested-doc-with-dot-notation pattern as auto_divider above: $set only
# touches the specific field, so concurrent updates to different sub-settings
# (e.g. dashboard changing the threshold while /security changes the log
# channel) never clobber each other.
# ---------------------------------------------------------
async def set_security_setting(guild_id: int, dotted_key: str, value) -> None:
    """Generic setter for any nested security.* field, e.g. 'anti_nuke.enabled'."""
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$set": {f"security.{dotted_key}": value, "guild_id": guild_id}},
        upsert=True,
    )


async def add_security_whitelist_user(guild_id: int, user_id: int) -> None:
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$addToSet": {"security.whitelist_user_ids": user_id}, "$set": {"guild_id": guild_id}},
        upsert=True,
    )


async def remove_security_whitelist_user(guild_id: int, user_id: int) -> None:
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$pull": {"security.whitelist_user_ids": user_id}},
    )


async def add_banned_word(guild_id: int, word: str) -> None:
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$addToSet": {"security.word_filter.banned_words": word.lower()}, "$set": {"guild_id": guild_id}},
        upsert=True,
    )


async def remove_banned_word(guild_id: int, word: str) -> None:
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$pull": {"security.word_filter.banned_words": word.lower()}},
    )


async def add_link_whitelist_domain(guild_id: int, domain: str) -> None:
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$addToSet": {"security.anti_link.whitelist_domains": domain.lower()}, "$set": {"guild_id": guild_id}},
        upsert=True,
    )


async def remove_link_whitelist_domain(guild_id: int, domain: str) -> None:
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$pull": {"security.anti_link.whitelist_domains": domain.lower()}},
    )


async def add_link_whitelist_channel(guild_id: int, channel_id: int) -> None:
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$addToSet": {"security.anti_link.whitelist_channel_ids": channel_id}, "$set": {"guild_id": guild_id}},
        upsert=True,
    )


async def remove_link_whitelist_channel(guild_id: int, channel_id: int) -> None:
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$pull": {"security.anti_link.whitelist_channel_ids": channel_id}},
    )


# ---------------------------------------------------------
# Leveling (Phase 2) - per-user XP/level, kept in its own collection since it's
# one document per (guild, member) rather than one per guild like everything
# above. Level itself is *derived* from xp (see utils/leveling_math.py) and
# also cached on the document so leaderboard sorts/queries don't need to
# recompute it for every row.
# ---------------------------------------------------------
async def add_xp(guild_id: int, member_id: int, amount: int, new_level: int) -> dict:
    """Adds XP and updates the cached level, returns the updated document."""
    doc = await _user_levels.find_one_and_update(
        {"guild_id": guild_id, "member_id": member_id},
        {"$inc": {"xp": amount}, "$set": {"level": new_level}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc


async def get_user_level(guild_id: int, member_id: int) -> dict:
    doc = await _user_levels.find_one({"guild_id": guild_id, "member_id": member_id})
    return doc or {"guild_id": guild_id, "member_id": member_id, "xp": 0, "level": 0}


async def get_leaderboard(guild_id: int, limit: int = 10) -> list:
    cursor = _user_levels.find({"guild_id": guild_id}).sort("xp", -1).limit(limit)
    return await cursor.to_list(length=limit)


async def get_rank_position(guild_id: int, member_id: int, member_xp: int) -> int:
    """1-indexed rank: how many members in this guild have strictly more XP, + 1."""
    higher_count = await _user_levels.count_documents({"guild_id": guild_id, "xp": {"$gt": member_xp}})
    return higher_count + 1


async def set_leveling_setting(guild_id: int, dotted_key: str, value) -> None:
    """Generic setter for nested leveling.* fields, e.g. 'xp_min'."""
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$set": {f"leveling.{dotted_key}": value, "guild_id": guild_id}},
        upsert=True,
    )


async def set_level_role(guild_id: int, level: int, role_id: int | None) -> None:
    """role_id=None removes the reward for that level."""
    if role_id is None:
        await _guild_settings.update_one(
            {"guild_id": guild_id},
            {"$unset": {f"leveling.level_roles.{level}": ""}},
        )
    else:
        await _guild_settings.update_one(
            {"guild_id": guild_id},
            {"$set": {f"leveling.level_roles.{level}": role_id, "guild_id": guild_id}},
            upsert=True,
        )


async def add_leveling_ignored_channel(guild_id: int, channel_id: int) -> None:
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$addToSet": {"leveling.ignored_channel_ids": channel_id}, "$set": {"guild_id": guild_id}},
        upsert=True,
    )


async def remove_leveling_ignored_channel(guild_id: int, channel_id: int) -> None:
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$pull": {"leveling.ignored_channel_ids": channel_id}},
    )


# ---------------------------------------------------------
# Reaction Roles (Phase 2) - one document per reaction-role message, keyed by
# message_id so the listener can do a single indexed lookup per reaction event.
# ---------------------------------------------------------
async def create_reaction_role_message(guild_id: int, channel_id: int, message_id: int) -> None:
    await _reaction_roles.update_one(
        {"message_id": message_id},
        {"$set": {"guild_id": guild_id, "channel_id": channel_id, "message_id": message_id, "mappings": {}}},
        upsert=True,
    )


async def add_reaction_role_mapping(message_id: int, emoji: str, role_id: int) -> None:
    await _reaction_roles.update_one(
        {"message_id": message_id},
        {"$set": {f"mappings.{emoji}": role_id}},
    )


async def remove_reaction_role_mapping(message_id: int, emoji: str) -> None:
    await _reaction_roles.update_one(
        {"message_id": message_id},
        {"$unset": {f"mappings.{emoji}": ""}},
    )


async def get_reaction_role_message(message_id: int) -> dict | None:
    return await _reaction_roles.find_one({"message_id": message_id})


async def delete_reaction_role_message(message_id: int) -> None:
    await _reaction_roles.delete_one({"message_id": message_id})


async def list_reaction_role_messages(guild_id: int) -> list:
    cursor = _reaction_roles.find({"guild_id": guild_id})
    return await cursor.to_list(length=100)


async def set_dashboard_branding_setting(guild_id: int, dotted_key: str, value) -> None:
    """Generic setter for nested dashboard_branding.* fields, e.g. 'product_name'."""
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$set": {f"dashboard_branding.{dotted_key}": value, "guild_id": guild_id}},
        upsert=True,
    )


async def set_voice_rooms_setting(guild_id: int, dotted_key: str, value) -> None:
    """Generic setter for nested voice_rooms.* fields, e.g. 'hub_channel_id'."""
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$set": {f"voice_rooms.{dotted_key}": value, "guild_id": guild_id}},
        upsert=True,
    )


async def create_voice_room(guild_id: int, channel_id: int, owner_id: int) -> None:
    await _voice_rooms.update_one(
        {"channel_id": channel_id},
        {"$set": {"guild_id": guild_id, "channel_id": channel_id, "owner_id": owner_id}},
        upsert=True,
    )


async def get_voice_room(channel_id: int) -> dict | None:
    return await _voice_rooms.find_one({"channel_id": channel_id})


async def set_voice_room_owner(channel_id: int, owner_id: int) -> None:
    await _voice_rooms.update_one({"channel_id": channel_id}, {"$set": {"owner_id": owner_id}})


async def delete_voice_room(channel_id: int) -> None:
    await _voice_rooms.delete_one({"channel_id": channel_id})


# ---------------------------------------------------------
# Analytics (Phase 4) - one document per (guild, calendar day, UTC), storing
# just the handful of counters the Overview page's charts need. Kept as its
# own tiny collection rather than folded into guild_settings since it's
# high-write-frequency (every message) and grows over time, unlike settings.
# ---------------------------------------------------------
def _today_str() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


async def increment_daily_stat(guild_id: int, field: str, amount: int = 1) -> None:
    """field is one of: messages, joins, leaves."""
    date = _today_str()
    await _guild_stats_daily.update_one(
        {"guild_id": guild_id, "date": date},
        {"$inc": {field: amount}, "$set": {"guild_id": guild_id, "date": date}},
        upsert=True,
    )


async def set_member_count_snapshot(guild_id: int, count: int) -> None:
    date = _today_str()
    await _guild_stats_daily.update_one(
        {"guild_id": guild_id, "date": date},
        {"$set": {"member_count_snapshot": count, "guild_id": guild_id, "date": date}},
        upsert=True,
    )


async def get_daily_stats(guild_id: int, days: int = 14) -> list:
    """Returns exactly `days` entries in chronological order (oldest first),
    zero-filled for any day with no activity, so the chart renderer never has
    to deal with gaps."""
    today = datetime.datetime.now(datetime.timezone.utc).date()
    date_strs = [(today - datetime.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]

    cursor = _guild_stats_daily.find({"guild_id": guild_id, "date": {"$in": date_strs}})
    by_date = {doc["date"]: doc async for doc in cursor}

    results = []
    last_known_snapshot = None
    for date_str in date_strs:
        doc = by_date.get(date_str, {})
        snapshot = doc.get("member_count_snapshot")
        if snapshot is not None:
            last_known_snapshot = snapshot
        results.append({
            "date": date_str,
            "messages": doc.get("messages", 0),
            "joins": doc.get("joins", 0),
            "leaves": doc.get("leaves", 0),
            "member_count_snapshot": last_known_snapshot,
        })
    return results


# ---------------------------------------------------------
# Log History (Phase 4) - a searchable record of everything that's ever been
# sent to any of the guild's log channels, written by the single shared
# utils/log_helper.send_guild_log() function every log-producing cog already
# calls - so this needed zero changes to moderation.py, voice_logs.py,
# audit_logs.py, server_logs.py, security.py, or setup.py to start working.
# ---------------------------------------------------------
async def record_event_log(guild_id: int, setting_key: str, title: str, description: str, color: int) -> None:
    await _event_logs.insert_one({
        "guild_id": guild_id,
        "setting_key": setting_key,
        "title": title or "",
        "description": description or "",
        "color": color,
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
    })


async def get_event_logs(guild_id: int, setting_key: str = None, search: str = None, limit: int = 50) -> list:
    query = {"guild_id": guild_id}
    if setting_key:
        query["setting_key"] = setting_key
    if search:
        query["$or"] = [
            {"title": {"$regex": search, "$options": "i"}},
            {"description": {"$regex": search, "$options": "i"}},
        ]
    cursor = _event_logs.find(query).sort("timestamp", -1).limit(min(limit, 200))
    return await cursor.to_list(length=min(limit, 200))
