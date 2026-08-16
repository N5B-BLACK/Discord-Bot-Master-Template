"""
Reaction Roles (Phase 2) - post a message, map emoji -> role, members react to
get the role and un-react to remove it.

Design choice: raw reaction events (on_raw_reaction_add/remove), not the
cached on_reaction_add, so this also works for messages that aren't in the
bot's message cache (e.g. after a restart) - a reaction-role panel is exactly
the kind of message that's often reacted to long after it was originally sent.
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils.db import (
    add_reaction_role_mapping,
    create_reaction_role_message,
    delete_reaction_role_message,
    get_guild_settings,
    get_reaction_role_message,
    list_reaction_role_messages,
    remove_reaction_role_mapping,
)
from utils.embed_helper import build_embed
from utils.licensing import is_module_available


class ReactionRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    reaction_roles_group = app_commands.Group(name="reactionroles", description="Set up self-assignable roles via reactions")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._handle_reaction(payload, adding=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._handle_reaction(payload, adding=False)

    async def _handle_reaction(self, payload: discord.RawReactionActionEvent, adding: bool) -> None:
        if payload.guild_id is None or (adding and payload.member and payload.member.bot):
            return

        doc = await get_reaction_role_message(payload.message_id)
        if doc is None:
            return

        settings = await get_guild_settings(payload.guild_id)
        if not is_module_available(settings, "reaction_roles"):
            return

        emoji_key = str(payload.emoji)
        role_id = doc.get("mappings", {}).get(emoji_key)
        if role_id is None:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        role = guild.get_role(role_id)
        member = guild.get_member(payload.user_id)
        if role is None or member is None or member.bot:
            return

        try:
            if adding:
                await member.add_roles(role, reason="Reaction role")
            else:
                await member.remove_roles(role, reason="Reaction role removed")
        except discord.Forbidden:
            pass

    # -----------------------------------------------------------------
    # Setup commands
    # -----------------------------------------------------------------
    @reaction_roles_group.command(name="create", description="Post a new reaction-role panel in a channel")
    @app_commands.checks.has_permissions(administrator=True)
    async def create(self, interaction: discord.Interaction, channel: discord.TextChannel, title: str, description: str = None):
        settings = await get_guild_settings(interaction.guild_id)
        if not is_module_available(settings, "reaction_roles"):
            await interaction.response.send_message(
                "Reaction Roles isn't available on this server's current plan.", ephemeral=True
            )
            return

        embed = await build_embed(interaction.guild_id, title=title, description=description or "React below to get a role.")
        message = await channel.send(embed=embed)
        await create_reaction_role_message(interaction.guild_id, channel.id, message.id)
        await interaction.response.send_message(
            f"Panel created in {channel.mention} (message ID `{message.id}`). "
            f"Use `/reactionroles add-role` to map emojis to roles on it.",
            ephemeral=True,
        )

    @reaction_roles_group.command(name="add-role", description="Map an emoji to a role on an existing panel")
    @app_commands.describe(message_id="The panel's message ID (shown when you created it)")
    @app_commands.checks.has_permissions(administrator=True)
    async def add_role(self, interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role):
        try:
            mid = int(message_id)
        except ValueError:
            await interaction.response.send_message("That doesn't look like a valid message ID.", ephemeral=True)
            return

        doc = await get_reaction_role_message(mid)
        if doc is None or doc.get("guild_id") != interaction.guild_id:
            await interaction.response.send_message("No reaction-role panel found with that message ID in this server.", ephemeral=True)
            return

        await add_reaction_role_mapping(mid, emoji, role.id)

        channel = interaction.guild.get_channel(doc["channel_id"])
        if channel is not None:
            try:
                message = await channel.fetch_message(mid)
                await message.add_reaction(emoji)
            except (discord.NotFound, discord.HTTPException):
                pass

        await interaction.response.send_message(f"{emoji} now grants {role.mention} on that panel.", ephemeral=True)

    @reaction_roles_group.command(name="remove-role", description="Remove an emoji->role mapping from a panel")
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_role(self, interaction: discord.Interaction, message_id: str, emoji: str):
        try:
            mid = int(message_id)
        except ValueError:
            await interaction.response.send_message("That doesn't look like a valid message ID.", ephemeral=True)
            return
        await remove_reaction_role_mapping(mid, emoji)
        await interaction.response.send_message(f"Removed the {emoji} mapping from that panel.", ephemeral=True)

    @reaction_roles_group.command(name="delete", description="Delete a reaction-role panel entirely")
    @app_commands.checks.has_permissions(administrator=True)
    async def delete(self, interaction: discord.Interaction, message_id: str):
        try:
            mid = int(message_id)
        except ValueError:
            await interaction.response.send_message("That doesn't look like a valid message ID.", ephemeral=True)
            return
        await delete_reaction_role_message(mid)
        await interaction.response.send_message("Panel deleted from the database (the Discord message itself is untouched).", ephemeral=True)

    @reaction_roles_group.command(name="list", description="List all reaction-role panels in this server")
    @app_commands.checks.has_permissions(administrator=True)
    async def list_panels(self, interaction: discord.Interaction):
        panels = await list_reaction_role_messages(interaction.guild_id)
        if not panels:
            await interaction.response.send_message("No reaction-role panels set up yet.", ephemeral=True)
            return
        lines = []
        for p in panels:
            mapping_count = len(p.get("mappings", {}))
            lines.append(f"• Message `{p['message_id']}` in <#{p['channel_id']}> — {mapping_count} role(s) mapped")
        embed = await build_embed(interaction.guild_id, title="Reaction Role Panels", description="\n".join(lines))
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionRoles(bot))
