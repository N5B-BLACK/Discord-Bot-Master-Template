"""
Shared helper - sends an embed to a guild's configured log channel for a given setting.
Used by multiple cogs (voice_logs, audit_logs, moderation, setup) to avoid repeating the
same logic. Also applies that log type's custom color override (set via the dashboard's
Server Settings page, next to the channel picker) if one is configured - this is the
single place that logic lives, so every log type gets it for free.
"""

import discord

from utils.db import get_guild_settings


async def send_guild_log(guild: discord.Guild, setting_key: str, embed: discord.Embed, **send_kwargs) -> None:
    settings = await get_guild_settings(guild.id)
    channel_id = settings.get(setting_key)
    if not channel_id:
        return
    channel = guild.get_channel(channel_id)
    if not channel:
        return

    color_override = (settings.get("log_colors") or {}).get(setting_key)
    if color_override is not None:
        embed.colour = discord.Color(color_override)

    await channel.send(embed=embed, **send_kwargs)
