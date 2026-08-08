"""
Trap channel - a honeypot channel (e.g. hidden, or labeled as something enticing to
raiders/scrapers) configured via the dashboard or /setup. Anyone who posts in it gets:
1. Their triggering message deleted immediately.
2. Banned, with Discord's own delete_message_seconds set to the maximum (7 days) -
   this purges ALL of that member's messages server-wide from the last week, not just
   the one that triggered the trap. This is the "delete everything of theirs" behavior
   Discord's ban API supports natively; there's no API to reach further back than 7
   days for a bulk purge-on-ban.
3. Logged to the ban/unban log channel (reuses the existing log - this IS a ban, just
   with a specific cause worth noting in the embed).

Exemptions: bots, and anyone who already has Administrator or the configured mod role -
without this, a mod accidentally posting in the trap channel (e.g. while setting it up)
would instantly ban themselves with no recovery path except another admin.
"""

import datetime

import discord
from discord.ext import commands

from utils.db import get_guild_settings
from utils.embed_helper import build_embed
from utils.log_helper import send_guild_log

TRAP_BAN_PURGE_SECONDS = 604800  # Discord's own maximum for delete_message_seconds (7 days)


class TrapChannel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _is_exempt(self, member: discord.Member, settings: dict) -> bool:
        if member.bot:
            return True
        if member.guild_permissions.administrator:
            return True
        mod_role_id = settings.get("mod_role_id")
        if mod_role_id and any(r.id == mod_role_id for r in member.roles):
            return True
        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return

        settings = await get_guild_settings(message.guild.id)
        trap_channel_id = settings.get("trap_channel_id")
        if not trap_channel_id or message.channel.id != trap_channel_id:
            return

        member = message.author
        if not isinstance(member, discord.Member) or self._is_exempt(member, settings):
            return

        try:
            await message.delete()
        except discord.HTTPException:
            pass  # the ban below still proceeds even if this specific delete fails

        try:
            await message.guild.ban(
                member,
                delete_message_seconds=TRAP_BAN_PURGE_SECONDS,
                reason="Posted in the trap channel (auto-moderation)",
            )
        except discord.Forbidden:
            # Can't ban (role hierarchy or missing permission) - at least the message is gone.
            # Notify the ban log so an admin knows manual action is needed.
            embed = await build_embed(
                message.guild.id,
                title="⚠️ Trap channel triggered - ban FAILED",
                color=discord.Color.orange().value,
                timestamp=datetime.datetime.utcnow(),
            )
            embed.add_field(name="Member", value=f"{member} ({member.id})", inline=True)
            embed.add_field(
                name="Issue", value="I don't have permission to ban this member - manual action needed.", inline=False
            )
            await send_guild_log(message.guild, "ban_unban_log_channel_id", embed)
            return

        embed = await build_embed(
            message.guild.id,
            title="🪤 Trap channel triggered - member banned",
            color=discord.Color.red().value,
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="Member", value=f"{member} ({member.id})", inline=True)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        embed.add_field(
            name="Action taken",
            value="Banned, with the last 7 days of their messages purged server-wide.",
            inline=False,
        )
        await send_guild_log(message.guild, "ban_unban_log_channel_id", embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(TrapChannel(bot))
