"""
Additional logs, each type going to its own channel (configured via /setup):
- Ban/unban
- Server join/leave (distinguishing a natural leave from a kick)
- Message deletion
- Timeout

Note: telling a "natural leave" apart from a "kick" requires checking the Audit Log
(same approach as the voice disconnect log) - the bot needs "View Audit Log" permission.
"""

import asyncio
import datetime

import discord
from discord.ext import commands

from utils.embed_helper import build_embed
from utils.log_helper import send_guild_log


class AuditLogs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _get_recent_audit_entry(self, guild: discord.Guild, action, target_id: int, wait: float = 1.5):
        """Checks the Audit Log for a recent entry (last 10 seconds) matching the target - returns the entry or None."""
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

    # ---------------------------------------------------------
    # Ban / unban
    # ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        entry = await self._get_recent_audit_entry(guild, discord.AuditLogAction.ban, user.id)

        embed = await build_embed(
            guild.id,
            title="🔨 Member banned",
            color=discord.Color.red().value,
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="Member", value=f"{user} ({user.id})", inline=True)
        if entry:
            embed.add_field(name="By", value=entry.user.mention, inline=True)
            if entry.reason:
                embed.add_field(name="Reason", value=entry.reason, inline=False)
        await send_guild_log(guild, "ban_unban_log_channel_id", embed)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        entry = await self._get_recent_audit_entry(guild, discord.AuditLogAction.unban, user.id)

        embed = await build_embed(
            guild.id,
            title="✅ Member unbanned",
            color=discord.Color.green().value,
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="Member", value=f"{user} ({user.id})", inline=True)
        if entry:
            embed.add_field(name="By", value=entry.user.mention, inline=True)
        await send_guild_log(guild, "ban_unban_log_channel_id", embed)

    # ---------------------------------------------------------
    # Joined the server
    # ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        embed = await build_embed(
            member.guild.id,
            title="📥 New member joined",
            color=discord.Color.green().value,
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="Member", value=member.mention, inline=True)
        embed.add_field(
            name="Account created",
            value=discord.utils.format_dt(member.created_at, style="R"),
            inline=True,
        )
        await send_guild_log(member.guild, "server_join_leave_log_channel_id", embed)

    # ---------------------------------------------------------
    # Left the server (natural leave or kick)
    # ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        entry = await self._get_recent_audit_entry(member.guild, discord.AuditLogAction.kick, member.id)

        if entry:
            embed = await build_embed(
                member.guild.id,
                title="👢 Member kicked",
                color=discord.Color.orange().value,
                timestamp=datetime.datetime.utcnow(),
            )
            embed.add_field(name="Member", value=f"{member} ({member.id})", inline=True)
            embed.add_field(name="By", value=entry.user.mention, inline=True)
            if entry.reason:
                embed.add_field(name="Reason", value=entry.reason, inline=False)
            await send_guild_log(member.guild, "kicked_log_channel_id", embed)
        else:
            embed = await build_embed(
                member.guild.id,
                title="📤 Member left the server",
                color=discord.Color.dark_grey().value,
                timestamp=datetime.datetime.utcnow(),
            )
            embed.add_field(name="Member", value=f"{member} ({member.id})", inline=True)
            await send_guild_log(member.guild, "server_join_leave_log_channel_id", embed)

    # ---------------------------------------------------------
    # Message deletion
    # ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return

        embed = await build_embed(
            message.guild.id,
            title="🗑️ Message deleted",
            color=discord.Color.dark_red().value,
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="Author", value=message.author.mention, inline=True)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        content = message.content or "*(no text - might be an image or file)*"
        embed.add_field(name="Content", value=content[:1024], inline=False)
        await send_guild_log(message.guild, "msg_deleted_log_channel_id", embed)

    # ---------------------------------------------------------
    # Timeout
    # ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.timed_out_until == after.timed_out_until:
            return

        entry = await self._get_recent_audit_entry(
            after.guild, discord.AuditLogAction.member_update, after.id
        )

        if after.timed_out_until and after.timed_out_until > discord.utils.utcnow():
            embed = await build_embed(
                after.guild.id,
                title="⏱️ Member timed out",
                color=discord.Color.dark_gold().value,
                timestamp=datetime.datetime.utcnow(),
            )
            embed.add_field(name="Member", value=after.mention, inline=True)
            embed.add_field(
                name="Until",
                value=discord.utils.format_dt(after.timed_out_until, style="R"),
                inline=True,
            )
        else:
            embed = await build_embed(
                after.guild.id,
                title="✅ Timeout removed",
                color=discord.Color.green().value,
                timestamp=datetime.datetime.utcnow(),
            )
            embed.add_field(name="Member", value=after.mention, inline=True)

        if entry:
            embed.add_field(name="By", value=entry.user.mention, inline=True)
            if entry.reason:
                embed.add_field(name="Reason", value=entry.reason, inline=False)

        await send_guild_log(after.guild, "timeout_log_channel_id", embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(AuditLogs(bot))
