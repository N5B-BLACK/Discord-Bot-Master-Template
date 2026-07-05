"""
نقطة البداية للبوت - Master Template
شغّل هذا الملف لتشغيل البوت: python main.py
"""

import asyncio
import logging

import discord
from discord.ext import commands

import config

# ---------------------------------------------------------
# إعداد الـ Logging (لتسجيل الأحداث والأخطاء بشكل منظم)
# ---------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("bot")

# ---------------------------------------------------------
# إعداد الـ Intents (الصلاحيات اللي البوت محتاجها)
# فعّل بس اللي فعلاً محتاجه - مبدأ "أقل صلاحيات ممكنة"
# ---------------------------------------------------------
intents = discord.Intents.default()
intents.members = True          # لازم لأوامر الموديريشن والترحيب
intents.message_content = True  # لازم عشان البوت يقرأ محتوى الرسائل (AI chat)

bot = commands.Bot(command_prefix=config.PREFIX, intents=intents)

# قائمة الـ Cogs (الملفات) اللي رح تنحمل - عطل/فعل حسب طلب كل عميل
COGS = [
    "cogs.moderation",
    "cogs.welcome",
    "cogs.ai_chat",
    "cogs.utility",
]


@bot.event
async def on_ready():
    logger.info(f"✅ البوت شغال الآن باسم: {bot.user} (ID: {bot.user.id})")
    logger.info(f"متصل بـ {len(bot.guilds)} سيرفر")

    # مزامنة الـ Slash Commands لسيرفر محدد فقط (تظهر فوراً بدل ساعة كاملة)
    if config.GUILD_ID:
        guild = discord.Object(id=config.GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        logger.info(f"تمت مزامنة {len(synced)} أمر Slash على السيرفر المحدد")


async def load_cogs():
    for cog in COGS:
        try:
            await bot.load_extension(cog)
            logger.info(f"تم تحميل: {cog}")
        except Exception as e:
            logger.error(f"فشل تحميل {cog}: {e}")


async def main():
    async with bot:
        await load_cogs()
        await bot.start(config.TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
