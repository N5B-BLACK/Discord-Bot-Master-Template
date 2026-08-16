"""
License admin (Phase 5 groundwork) - bot-owner-only commands to inspect or set
a guild's plan. Exists mainly so the licensing mechanism in utils/licensing.py
is actually testable by hand before any automated (e.g. payment-webhook-driven)
way of setting plans exists. Every guild defaults to plan="unlimited", which
always passes every check - these commands are the only way today to change
that, and only the bot owner (you) can run them.
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils.db import get_guild_settings, set_license


class LicenseAdmin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    license_group = app_commands.Group(name="license", description="[Owner only] View or set a server's plan")

    @license_group.command(name="status", description="[Owner only] View a server's current plan")
    @app_commands.describe(guild_id="The server's ID (defaults to the current server)")
    async def status(self, interaction: discord.Interaction, guild_id: str = None):
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        target_id = int(guild_id) if guild_id else interaction.guild_id
        settings = await get_guild_settings(target_id)
        license_info = settings.get("license", {})
        await interaction.response.send_message(
            f"Guild `{target_id}`: plan=**{license_info.get('plan', 'unlimited')}**, "
            f"expires_at=`{license_info.get('expires_at') or 'never'}`",
            ephemeral=True,
        )

    @license_group.command(name="set", description="[Owner only] Set a server's plan")
    @app_commands.describe(
        guild_id="The server's ID (defaults to the current server)",
        plan="free / pro / unlimited",
        expires_at="ISO date (e.g. 2026-12-31), or leave blank for no expiry",
    )
    @app_commands.choices(
        plan=[
            app_commands.Choice(name="Free", value="free"),
            app_commands.Choice(name="Pro", value="pro"),
            app_commands.Choice(name="Unlimited (internal/no restrictions)", value="unlimited"),
        ]
    )
    async def set_plan(self, interaction: discord.Interaction, plan: app_commands.Choice[str], guild_id: str = None, expires_at: str = None):
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        target_id = int(guild_id) if guild_id else interaction.guild_id
        await set_license(target_id, plan.value, expires_at)
        await interaction.response.send_message(
            f"Guild `{target_id}` set to plan **{plan.value}**{f', expires {expires_at}' if expires_at else ''}.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(LicenseAdmin(bot))
