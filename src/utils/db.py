"""
طبقة الوصول لقاعدة بيانات MongoDB - تخزين إعدادات كل سيرفر (guild) بشكل منفصل.
كل سيرفر إله document خاص فيه، مفتاحه guild_id.
"""

import motor.motor_asyncio
from pymongo import ReturnDocument

import config

_client = motor.motor_asyncio.AsyncIOMotorClient(config.MONGODB_URI)
_db = _client["discord_bot"]
_guild_settings = _db["guild_settings"]
_tickets = _db["tickets"]
_counters = _db["ticket_counters"]


async def check_connection() -> bool:
    """يعمل ping بسيط لقاعدة البيانات - يرجع True لو الاتصال شغال، ويرفع استثناء لو في مشكلة."""
    await _client.admin.command("ping")
    return True

# القيم الافتراضية - لو ما في إعداد محفوظ لسيرفر معين، بترجع هاي القيم (يعني بدون أي تقييد)
DEFAULT_SETTINGS = {
    "mod_role_id": None,
    "mod_log_channel_id": None,
    "welcome_channel_id": None,
    "auto_role_id": None,
    "ai_chat_channel_id": None,
    "ticket_support_role_id": None,
}


async def get_guild_settings(guild_id: int) -> dict:
    """يرجع إعدادات السيرفر المحفوظة، أو القيم الافتراضية لو ما ضبط الأدمن شي بعد."""
    doc = await _guild_settings.find_one({"guild_id": guild_id})
    settings = DEFAULT_SETTINGS.copy()
    if doc:
        settings.update({k: v for k, v in doc.items() if k in DEFAULT_SETTINGS})
    return settings


async def update_guild_setting(guild_id: int, key: str, value) -> None:
    """يحدّث إعداد واحد لسيرفر معين (وينشئ الـ document لو أول مرة)."""
    await _guild_settings.update_one(
        {"guild_id": guild_id},
        {"$set": {key: value, "guild_id": guild_id}},
        upsert=True,
    )


async def create_ticket(guild_id: int, thread_id: int, opener_id: int) -> None:
    """يسجل تذكرة جديدة - مين فتحها ولسا ما استلمها حدا."""
    await _tickets.update_one(
        {"thread_id": thread_id},
        {"$set": {"guild_id": guild_id, "opener_id": opener_id, "claimed_by": None}},
        upsert=True,
    )


async def get_ticket(thread_id: int):
    """يرجع بيانات التذكرة (guild_id, opener_id, claimed_by)، أو None لو ما لقاها."""
    return await _tickets.find_one({"thread_id": thread_id})


async def set_ticket_claim(thread_id: int, staff_id: int) -> None:
    """يسجل مين استلم التذكرة."""
    await _tickets.update_one({"thread_id": thread_id}, {"$set": {"claimed_by": staff_id}})


async def get_next_ticket_number(guild_id: int) -> int:
    """يرجع رقم تسلسلي جديد للتذكرة (يبدأ من 1 لكل سيرفر لحاله) - عملية ذرية (atomic) فما بيصير تكرار."""
    doc = await _counters.find_one_and_update(
        {"guild_id": guild_id},
        {"$inc": {"count": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc["count"]
