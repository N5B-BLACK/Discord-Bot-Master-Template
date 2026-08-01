"""
Unified error handling for slash commands (app_commands) -
instead of the bot silently failing on an error, it replies with a clear message
to the user and logs the details.
"""

import logging

import discord
from discord import app_commands

logger = logging.getLogger("bot")


async def handle_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        message = "🚫 You don't have permission to use this command."

    elif isinstance(error, app_commands.CheckFailure):
        # covers custom channel/role check failures (see checks.py)
        message = "⚠️ You can't use this command here (wrong channel or missing role)."

    else:
        logger.error(f"Unexpected error in command {interaction.command}: {error}", exc_info=True)
        message = "⚠️ An unexpected error occurred while running this command. It has been logged."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


def setup_error_handling(bot):
    @bot.tree.error
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        await handle_app_command_error(interaction, error)
