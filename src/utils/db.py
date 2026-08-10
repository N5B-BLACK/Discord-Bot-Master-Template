"""
MongoDB access layer - stores each server's (guild) settings separately.
Each guild has its own document, keyed by guild_id.
"""

import motor.motor_asyncio
from pymongo import ReturnDocument

import config

_client = motor.motor_asyncio.AsyncIOMotorClient(config.MONGODB_URI)
_db = _client["discord_bot"]
_guild_settings = _db["guild_settings"]
_tickets = _db["tickets"]
_counters = _db["ticket_counters"]
_warnings = _db["warnings"]
_embed_drafts = _db["embed_drafts"]


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
