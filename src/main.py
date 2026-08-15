"""
Bot entry point - Master Template
Run this file to start the bot: python main.py
"""

import asyncio
import logging
import os

import discord
from aiohttp import web
from discord.ext import commands

import config
from dashboard import setup_dashboard_routes
from utils.db import check_connection
from utils.error_handler import setup_error_handling

# ---------------------------------------------------------
# Logging setup
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
# Intents - only enable what's actually needed
# ---------------------------------------------------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True  # required for on_voice_state_update (voice logs)

bot = commands.Bot(command_prefix=config.PREFIX, intents=intents)
setup_error_handling(bot)

COGS = [
    "cogs.moderation",
    "cogs.welcome",
    "cogs.ai_chat",
    "cogs.utility",
    "cogs.setup",
    "cogs.tickets",
    "cogs.embed_builder",
    "cogs.music",
    "cogs.voice_logs",
    "cogs.audit_logs",
    "cogs.server_logs",
    "cogs.trap_channel",
    "cogs.auto_divider",
    "cogs.security",
    "cogs.leveling",
    "cogs.reaction_roles",
    "cogs.voice_rooms",
    "cogs.stats",
]


@bot.event
async def on_ready():
    logger.info(f"✅ Bot is online as: {bot.user} (ID: {bot.user.id})")
    logger.info(f"Connected to {len(bot.guilds)} server(s)")

    try:
        await check_connection()
        logger.info("✅ Database (MongoDB) connection is healthy")
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")

    if config.GUILD_ID:
        guild = discord.Object(id=config.GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        logger.info(f"Synced {len(synced)} slash command(s) to the configured guild")
    else:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} slash command(s) globally")


async def load_cogs():
    for cog in COGS:
        try:
            await bot.load_extension(cog)
            logger.info(f"Loaded: {cog}")
        except Exception as e:
            logger.error(f"Failed to load {cog}: {e}")


# ---------------------------------------------------------
# Minimal web server - just so Render considers the service "up"
# and UptimeRobot has something to ping. Unrelated to bot logic.
# ---------------------------------------------------------
async def health(request):
    return web.Response(text="Bot is alive!")


async def start_webserver():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    setup_dashboard_routes(app, bot)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))  # Render sets PORT automatically
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health check + dashboard server running on port {port}")


async def main():
    async with bot:
        await load_cogs()
        await start_webserver()
        await bot.start(config.TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
