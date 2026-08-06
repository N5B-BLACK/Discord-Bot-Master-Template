"""
Simple general-purpose commands: ping, info, say.
"""

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.checks import has_configured_role


class SayModal(discord.ui.Modal, title="Send a message"):
    """
    A Modal (not a plain string command option) is deliberate: Discord's slash-command
    string parameters render as a single-line field, and pasting multi-line/paragraph
    text into one commonly mangles it (newlines get collapsed or stripped client-side -
    this is a Discord client behavior, not something fixable from the bot side). A
    Modal's paragraph-style TextInput is a real multi-line box and preserves pasted
    text exactly, which is why every polished bot uses this pattern for /say-style
    commands instead of a plain string option.
    """

    message = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        max_length=2000,  # Discord's own hard limit for a single message
        placeholder="Type or paste your message here - multi-line is preserved exactly...",
    )

    def __init__(self, channel: discord.TextChannel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await self.channel.send(self.message.value)
        except discord.Forbidden:
            await interaction.response.send_message(
                f"🚫 I don't have permission to post in {self.channel.mention}.", ephemeral=True
            )
            return
        await interaction.response.send_message(f"✅ Message sent in {self.channel.mention}.", ephemeral=True)


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
    @app_commands.describe(channel="The channel to send the message in")
    @has_configured_role("mod_role_id")
    async def say(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.send_modal(SayModal(channel))


async def setup(bot: commands.Bot):
    await bot.add_cog(Utility(bot))
