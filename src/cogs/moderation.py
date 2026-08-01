"""
Core moderation commands: kick, ban, mute, warn.
All commands are gated by the per-server mod role (configured via /setup), not a static .env value.
Warnings are stored in MongoDB (not a local file) so they survive redeploys.
"""

import datetime

import discord
from discord import app_commands
from discord.ext import commands

from utils.checks import has_configured_role
from utils.db import add_warning, get_warnings
from utils.embed_helper import build_embed
from utils.log_helper import send_guild_log


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _log_warn(self, guild: discord.Guild, author, member: discord.Member, reason: str):
        embed = await build_embed(guild.id, title="⚠️ Warn")
        embed.timestamp = datetime.datetime.utcnow()
        embed.add_field(name="Member", value=f"{member} ({member.id})")
        embed.add_field(name="By", value=author.mention)
        embed.add_field(name="Reason", value=reason or "Not specified", inline=False)
        await send_guild_log(guild, "warn_log_channel_id", embed)

    @app_commands.command(name="kick", description="Kick a member from the server")
    @app_commands.describe(member="The member to kick", reason="Reason for the kick")
    @has_configured_role("mod_role_id")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = None):
        await member.kick(reason=reason)
        await interaction.response.send_message(f"👢 Kicked {member.mention}. Reason: {reason or 'Not specified'}")
        # Note: this is automatically logged to the "kick log" channel (audit_logs.py)

    @app_commands.command(name="ban", description="Ban a member from the server")
    @app_commands.describe(member="The member to ban", reason="Reason for the ban")
    @has_configured_role("mod_role_id")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = None):
        await member.ban(reason=reason)
        await interaction.response.send_message(f"🔨 Banned {member.mention}. Reason: {reason or 'Not specified'}")
        # Note: this is automatically logged to the "ban/unban log" channel (audit_logs.py)

    @app_commands.command(name="mute", description="Temporarily timeout a member")
    @app_commands.describe(member="The member to mute", minutes="Duration in minutes", reason="Reason")
    @has_configured_role("mod_role_id")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mute(self, interaction: discord.Interaction, member: discord.Member, minutes: int = 10, reason: str = None):
        duration = datetime.timedelta(minutes=minutes)
        await member.timeout(duration, reason=reason)
        await interaction.response.send_message(f"🔇 Timed out {member.mention} for {minutes} minutes.")
        # Note: this is automatically logged to the "timeout log" channel (audit_logs.py)

    @app_commands.command(name="warn", description="Warn a member")
    @app_commands.describe(member="The member to warn", reason="Reason for the warning")
    @has_configured_role("mod_role_id")
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str = None):
        entries = await add_warning(interaction.guild.id, member.id, reason, interaction.user.id)

        await interaction.response.send_message(
            f"⚠️ Warned {member.mention}. Total warnings: {len(entries)}"
        )
        await self._log_warn(interaction.guild, interaction.user, member, reason)

    @app_commands.command(name="warnings", description="View a member's warnings")
    @app_commands.describe(member="The member to check")
    @has_configured_role("mod_role_id")
    async def list_warnings(self, interaction: discord.Interaction, member: discord.Member):
        entries = await get_warnings(interaction.guild.id, member.id)
        if not entries:
            await interaction.response.send_message(f"✅ {member.mention} has no warnings.")
            return

        embed = await build_embed(interaction.guild.id, title=f"Warnings for {member}")
        for i, w in enumerate(entries, start=1):
            embed.add_field(name=f"#{i} - {w['date']}", value=w["reason"], inline=False)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
