"""
Simple general-purpose commands: ping, info, say.
"""

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.checks import has_configured_role


class Utility(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Check the bot's response time")
    async def ping(self, interaction: discord.Interaction):
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"🏓 Pong! ({latency_ms}ms)")

    @app_commands.command(name="info", description="Information about the bot")
    async def info(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title=config.BOT_NAME,
            description="A custom bot built on the Master Template.",
            color=config.EMBED_COLOR,
        )
        embed.add_field(name="Servers", value=str(len(self.bot.guilds)))
        embed.add_field(name="Available commands", value="Use `/` to see all commands")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="say", description="Makes the bot post a message in a chosen channel")
    @app_commands.describe(channel="The channel to send the message in", message="The message text")
    @has_configured_role("mod_role_id")
    async def say(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        message: str,
    ):
        try:
            await channel.send(message)
        except discord.Forbidden:
            await interaction.response.send_message(
                f"🚫 I don't have permission to post in {channel.mention}."
            )
            return

        await interaction.response.send_message(
            f"✅ Message sent in {channel.mention}."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Utility(bot))
