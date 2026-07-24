"""
نقطة البداية للبوت - Master Template
شغّل هذا الملف لتشغيل البوت: python main.py
"""

import asyncio
import logging
import os

import discord
from aiohttp import web
from discord.ext import commands

import config
from utils.db import check_connection
from utils.error_handler import setup_error_handling

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
# ---------------------------------------------------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix=config.PREFIX, intents=intents)
setup_error_handling(bot)

COGS = [
    "cogs.moderation",
    "cogs.welcome",
    "cogs.ai_chat",
    "cogs.utility",
    "cogs.setup",
    "cogs.tickets",
]


@bot.event
async def on_ready():
    logger.info(f"✅ البوت شغال الآن باسم: {bot.user} (ID: {bot.user.id})")
    logger.info(f"متصل بـ {len(bot.guilds)} سيرفر")

    try:
        await check_connection()
        logger.info("✅ الاتصال بقاعدة البيانات (MongoDB) شغال تمام")
    except Exception as e:
        logger.error(f"❌ فشل الاتصال بقاعدة البيانات: {e}")

    if config.GUILD_ID:
        guild = discord.Object(id=config.GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        logger.info(f"تمت مزامنة {len(synced)} أمر Slash على السيرفر المحدد")
    else:
        synced = await bot.tree.sync()
        logger.info(f"تمت مزامنة {len(synced)} أمر Slash بشكل عام")


async def load_cogs():
    for cog in COGS:
        try:
            await bot.load_extension(cog)
            logger.info(f"تم تحميل: {cog}")
        except Exception as e:
            logger.error(f"فشل تحميل {cog}: {e}")


# ---------------------------------------------------------
# سيرفر ويب بسيط جدًا - بس عشان Render يعتبر الخدمة "شغالة"
# و UptimeRobot يقدر يعمله ping. ما إله علاقة بمنطق البوت.
# ---------------------------------------------------------
async def health(request):
    return web.Response(text="Bot is alive!")


async def start_webserver():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))  # Render بيحدد PORT تلقائيًا
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"سيرفر الـ health check شغال على المنفذ {port}")


async def main():
    async with bot:
        await load_cogs()
        await start_webserver()
        await bot.start(config.TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
