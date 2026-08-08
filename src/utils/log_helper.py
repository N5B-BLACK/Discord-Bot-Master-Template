"""
Shared helper - sends an embed to a guild's configured log channel for a given setting.
Used by multiple cogs (voice_logs, audit_logs, server_logs, moderation, setup) to avoid
repeating the same logic. Also applies that log type's custom color override (set via
the dashboard's Server Settings page, next to the channel picker) if one is configured -
this is the single place that logic lives, so every log type gets it for free.
"""

import asyncio

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


async def get_recent_audit_entry(guild: discord.Guild, action, target_id: int, wait: float = 1.5):
    """
    Checks the Audit Log for a recent entry (created within the last 10 seconds)
    whose target matches target_id - returns the entry, or None if nothing matches
    or the bot lacks the View Audit Log permission. `wait` gives Discord's audit log a
    moment to actually record the entry before we look (it isn't always instant).
    """
    await asyncio.sleep(wait)
    try:
        async for entry in guild.audit_logs(limit=5, action=action):
            if entry.target and getattr(entry.target, "id", None) == target_id:
                time_diff = discord.utils.utcnow() - entry.created_at
                if time_diff.total_seconds() < 10:
                    return entry
    except discord.Forbidden:
        pass  # bot lacks the View Audit Log permission
    return None
