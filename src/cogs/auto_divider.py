"""
Auto Divider - posts a configured image immediately after every message a human sends
in one or more chosen channels (visually separates each message/post, like a banner
or divider line - common in showcase/media channels). Fully dashboard-configurable:
the image URL and which channels it applies to are both set from the dashboard's
"Auto Divider" page, not hardcoded - see utils/db.py's auto_divider helpers and
dashboard.py's divider_page.

Critical: must never react to its own divider posts, or to other bots - that would
either loop forever or spam unnecessarily.
"""

import logging

import discord
from discord.ext import commands

from utils.db import get_guild_settings

logger = logging.getLogger("bot")


class AutoDivider(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return

        settings = await get_guild_settings(message.guild.id)
        divider = settings.get("auto_divider") or {}
        if not divider.get("enabled"):
            return
        image_url = divider.get("image_url")
        channel_ids = divider.get("channel_ids") or []
        if not image_url or message.channel.id not in channel_ids:
            return

        embed = discord.Embed()
        embed.set_image(url=image_url)
        try:
            await message.channel.send(embed=embed)
        except discord.HTTPException as e:
            logger.warning(f"[auto_divider] Couldn't post divider image in guild {message.guild.id}: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoDivider(bot))
