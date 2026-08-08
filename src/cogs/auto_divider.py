"""
Auto Divider - posts a configured image immediately after every message a human sends
in one or more chosen channels (visually separates each message/post, like a banner
or divider line - common in showcase/media channels). Fully dashboard-configurable:
the image URL and which channels it applies to are both set from the dashboard's
"Auto Divider" page, not hardcoded - see utils/db.py's auto_divider helpers and
dashboard.py's divider_page.

Sent as a plain file attachment, NOT a discord.Embed - a rich embed always renders
with Discord's card chrome (a colored accent bar on the left edge, a background box)
even with nothing but an image set, which looks like an unstyled/broken embed rather
than a clean banner. A raw attachment has none of that: it appears exactly like a
normal image post, edge-to-edge, matching how polished divider bots do this.

The image bytes are cached in memory per URL (see _image_cache) so a frequently-firing
divider doesn't re-download the same file on every single message - only the first
trigger after the image URL changes pays for a fetch.

Critical: must never react to its own divider posts, or to other bots - that would
either loop forever or spam unnecessarily.
"""

import io
import logging

import aiohttp
import discord
from discord.ext import commands

from utils.db import get_guild_settings

logger = logging.getLogger("bot")

FETCH_TIMEOUT = aiohttp.ClientTimeout(total=10)

# Simple process-wide cache: {image_url: (bytes, filename)}. Small and unbounded is fine
# here - in practice a server sets one, maybe a handful, of divider images total, not
# hundreds, so this never grows large enough to matter.
_image_cache: dict[str, tuple[bytes, str]] = {}


def _filename_from_url(url: str) -> str:
    name = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    if "." not in name:
        name += ".png"
    return name or "divider.png"


async def _get_divider_file(image_url: str) -> discord.File | None:
    cached = _image_cache.get(image_url)
    if cached:
        data, filename = cached
        return discord.File(io.BytesIO(data), filename=filename)

    try:
        async with aiohttp.ClientSession(timeout=FETCH_TIMEOUT) as session:
            async with session.get(image_url) as resp:
                if resp.status != 200:
                    logger.warning(f"[auto_divider] Image fetch got HTTP {resp.status} for {image_url}")
                    return None
                data = await resp.read()
    except Exception as e:
        logger.warning(f"[auto_divider] Couldn't fetch divider image {image_url}: {e}")
        return None

    filename = _filename_from_url(image_url)
    _image_cache[image_url] = (data, filename)
    return discord.File(io.BytesIO(data), filename=filename)


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

        file = await _get_divider_file(image_url)
        if file is None:
            return  # already logged in _get_divider_file; nothing sane to send

        try:
            await message.channel.send(file=file)
        except discord.HTTPException as e:
            logger.warning(f"[auto_divider] Couldn't post divider image in guild {message.guild.id}: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoDivider(bot))

