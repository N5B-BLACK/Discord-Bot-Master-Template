"""
Reusable checks - based on per-server settings stored in the database
(configured via /setup), not static values in .env.
"""

from discord import app_commands, Interaction

from utils.db import get_guild_settings


def in_configured_channel(setting_key: str):
    """
    Only allows execution in the channel saved under setting_key for this server.
    If the admin hasn't set a channel yet (via /setup), allows any channel.
    """

    async def predicate(interaction: Interaction) -> bool:
        settings = await get_guild_settings(interaction.guild_id)
        channel_id = settings.get(setting_key)
        if channel_id is None:
            return True
        return interaction.channel_id == channel_id

    return app_commands.check(predicate)


def has_configured_role(setting_key: str):
    """
    Only allows execution for members who have the role saved under setting_key for this server.
    If the admin hasn't set a role yet (via /setup), allows everyone.
    """

    async def predicate(interaction: Interaction) -> bool:
        settings = await get_guild_settings(interaction.guild_id)
        role_id = settings.get(setting_key)
        if role_id is None:
            return True
        return any(role.id == role_id for role in interaction.user.roles)

    return app_commands.check(predicate)
