"""
Branded embed builder - applies each server's custom color, icon (thumbnail), and
footer (configured via the dashboard's Branding section, or /setup) to embeds the
bot sends. This is the single place that knows about branding; cogs just call
build_embed() instead of discord.Embed() directly.

Note: Discord does not allow custom fonts in embeds via the API - this is a
platform-wide restriction, not something any bot can work around. Color, thumbnail
image, and footer (text + small icon) are the customizable pieces.
"""

import discord

from utils.db import get_guild_settings

DEFAULT_COLOR = 0x5865F2


async def build_embed(
    guild_id: int,
    title: str = None,
    description: str = None,
    color: int = None,
    use_brand_thumbnail: bool = True,
    **kwargs,
) -> discord.Embed:
    """
    Builds an embed using this server's branding.
    - color: pass an explicit color (e.g. red for an error) to override the brand color
      for this specific embed; otherwise the server's configured brand color is used.
    - use_brand_thumbnail: set False for embeds that already set their own thumbnail
      (e.g. a ticket embed showing the requester's avatar).
    """
    settings = await get_guild_settings(guild_id)
    final_color = color if color is not None else (settings.get("embed_color") or DEFAULT_COLOR)

    embed = discord.Embed(title=title, description=description, color=final_color, **kwargs)

    if use_brand_thumbnail and settings.get("embed_icon_url"):
        embed.set_thumbnail(url=settings["embed_icon_url"])

    footer_text = settings.get("embed_footer_text")
    if footer_text:
        embed.set_footer(text=footer_text, icon_url=settings.get("embed_footer_icon_url"))

    return embed
