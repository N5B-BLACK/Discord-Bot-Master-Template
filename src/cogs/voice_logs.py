"""
Voice logs - each event type goes to its own channel (configured via /setup page 3):
- Join/leave a voice channel
- Switch between two voice channels
- Member disconnected by an admin (Disconnect)
- Server mute/unmute
- Server deafen/undeafen

Note: telling apart a "natural leave" from a "forced disconnect by admin" isn't
directly available from the voice_state_update event, so we wait a second and check
the Audit Log for a matching MEMBER_DISCONNECT entry. Requires "View Audit Log" permission.
"""

import asyncio
import datetime

import discord
from discord.ext import commands

from utils.embed_helper import build_embed
from utils.log_helper import send_guild_log


class VoiceLogs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _check_forced_disconnect(self, member: discord.Member) -> discord.Member | None:
        """Checks the Audit Log for a forced disconnect of this member in the last few seconds -
        returns the responsible moderator if found, otherwise None."""
        await asyncio.sleep(1.5)  # give the Audit Log a moment to be written
        try:
            async for entry in member.guild.audit_logs(
                limit=5, action=discord.AuditLogAction.member_disconnect
            ):
                if entry.target and entry.target.id == member.id:
                    time_diff = discord.utils.utcnow() - entry.created_at
                    if time_diff.total_seconds() < 10:
                        return entry.user
        except discord.Forbidden:
            pass  # bot lacks the View Audit Log permission
        return None

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ):
        guild_id = member.guild.id

        # 1) joined for the first time
        if before.channel is None and after.channel is not None:
            embed = await build_embed(
                guild_id, title="🎙️ Joined a voice channel", color=discord.Color.green().value
            )
            embed.timestamp = datetime.datetime.utcnow()
            embed.add_field(name="Member", value=member.mention, inline=True)
            embed.add_field(name="Channel", value=after.channel.mention, inline=True)
            await send_guild_log(member.guild, "voice_join_leave_log_channel_id", embed)

        # 2) left entirely (or was disconnected by an admin)
        elif before.channel is not None and after.channel is None:
            forced_by = await self._check_forced_disconnect(member)
            if forced_by:
                embed = await build_embed(
                    guild_id, title="🚫 Member disconnected from voice", color=discord.Color.red().value
                )
                embed.timestamp = datetime.datetime.utcnow()
                embed.add_field(name="Member", value=member.mention, inline=True)
                embed.add_field(name="By", value=forced_by.mention, inline=True)
                embed.add_field(name="Channel", value=before.channel.mention, inline=True)
                await send_guild_log(member.guild, "voice_disconnect_log_channel_id", embed)
            else:
                embed = await build_embed(
                    guild_id, title="🎙️ Left a voice channel", color=discord.Color.orange().value
                )
                embed.timestamp = datetime.datetime.utcnow()
                embed.add_field(name="Member", value=member.mention, inline=True)
                embed.add_field(name="Channel", value=before.channel.mention, inline=True)
                await send_guild_log(member.guild, "voice_join_leave_log_channel_id", embed)

        # 3) switched between two channels
        elif (
            before.channel is not None
            and after.channel is not None
            and before.channel.id != after.channel.id
        ):
            embed = await build_embed(
                guild_id, title="🔀 Switched voice channel", color=discord.Color.blurple().value
            )
            embed.timestamp = datetime.datetime.utcnow()
            embed.add_field(name="Member", value=member.mention, inline=True)
            embed.add_field(name="From", value=before.channel.mention, inline=True)
            embed.add_field(name="To", value=after.channel.mention, inline=True)
            await send_guild_log(member.guild, "voice_switch_log_channel_id", embed)

        # 4) server mute/unmute - not self-mute
        if before.mute != after.mute:
            action = "🔇 Server muted" if after.mute else "🔊 Server unmuted"
            embed = await build_embed(guild_id, title=action, color=discord.Color.dark_gold().value)
            embed.timestamp = datetime.datetime.utcnow()
            embed.add_field(name="Member", value=member.mention, inline=True)
            if after.channel:
                embed.add_field(name="Channel", value=after.channel.mention, inline=True)
            await send_guild_log(member.guild, "voice_mute_log_channel_id", embed)

        # 5) server deafen/undeafen - not self-deafen
        if before.deaf != after.deaf:
            action = "🔕 Server deafened" if after.deaf else "🔔 Server undeafened"
            embed = await build_embed(guild_id, title=action, color=discord.Color.dark_gold().value)
            embed.timestamp = datetime.datetime.utcnow()
            embed.add_field(name="Member", value=member.mention, inline=True)
            if after.channel:
                embed.add_field(name="Channel", value=after.channel.mention, inline=True)
            await send_guild_log(member.guild, "voice_deafen_log_channel_id", embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceLogs(bot))
