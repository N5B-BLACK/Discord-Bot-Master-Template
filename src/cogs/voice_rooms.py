"""
Voice Rooms / "Groups" (Phase 3) - a member joins a designated hub voice
channel, the bot creates a fresh private voice channel for them and moves
them into it, and they get owner controls over it (lock, rename, limit, kick,
permit, block, transfer). The channel is deleted automatically once it's empty.

This is one of the highest-perceived-value features for a community server -
it turns every voice channel into a self-service tool members use constantly,
which is exactly the kind of feature that makes a paying client's members
notice the bot is there.

Ownership is tracked in the `voice_rooms` collection (channel_id -> owner_id),
not by checking Discord permission overwrites directly, since overwrites alone
can't distinguish "the owner" from "someone the owner permitted" - both would
otherwise look identical to a permissions-only check.
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils.db import (
    create_voice_room,
    delete_voice_room,
    get_guild_settings,
    get_voice_room,
    set_voice_room_owner,
)


class VoiceRooms(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    room_group = app_commands.Group(name="room", description="Control your private voice room")

    # -----------------------------------------------------------------
    # Join-to-create + auto-delete-when-empty
    # -----------------------------------------------------------------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        settings = await get_guild_settings(member.guild.id)
        conf = settings.get("voice_rooms", {})
        if not conf.get("enabled"):
            return

        hub_id = conf.get("hub_channel_id")
        if hub_id and after.channel is not None and after.channel.id == hub_id:
            await self._create_room_for(member, member.guild, conf)

        # If they left a tracked room, check whether it's now empty and clean it up.
        if before.channel is not None and before.channel != after.channel:
            room = await get_voice_room(before.channel.id)
            if room is not None and len(before.channel.members) == 0:
                try:
                    await before.channel.delete(reason="Voice room empty")
                except discord.NotFound:
                    pass
                except discord.Forbidden:
                    pass
                await delete_voice_room(before.channel.id)

    async def _create_room_for(self, member: discord.Member, guild: discord.Guild, conf: dict) -> None:
        hub_channel = guild.get_channel(conf.get("hub_channel_id"))
        category_id = conf.get("category_id")
        category = guild.get_channel(category_id) if category_id else (hub_channel.category if hub_channel else None)

        name = conf.get("name_template", "{username}'s Room").format(username=member.display_name)
        user_limit = conf.get("default_user_limit", 0)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(connect=True),
            member: discord.PermissionOverwrite(
                connect=True, manage_channels=True, move_members=True, mute_members=True, deafen_members=True
            ),
        }
        try:
            channel = await guild.create_voice_channel(
                name=name[:100],
                category=category,
                user_limit=user_limit,
                overwrites=overwrites,
                reason=f"Voice room created for {member}",
            )
            await member.move_to(channel, reason="Voice room created")
        except discord.Forbidden:
            return

        await create_voice_room(guild.id, channel.id, member.id)

    # -----------------------------------------------------------------
    # Owner controls
    # -----------------------------------------------------------------
    async def _get_owned_room(self, interaction: discord.Interaction) -> discord.VoiceChannel | None:
        """Returns the member's current voice channel if they own it (as a tracked
        room), replying with an error and returning None otherwise."""
        member = interaction.user
        if member.voice is None or member.voice.channel is None:
            await interaction.response.send_message("You need to be in a voice room to use this.", ephemeral=True)
            return None
        room = await get_voice_room(member.voice.channel.id)
        if room is None:
            await interaction.response.send_message("This voice channel isn't a managed voice room.", ephemeral=True)
            return None
        if room["owner_id"] != member.id:
            await interaction.response.send_message("Only the room owner can do that.", ephemeral=True)
            return None
        return member.voice.channel

    @room_group.command(name="lock", description="Stop new members from joining your room")
    async def lock(self, interaction: discord.Interaction):
        channel = await self._get_owned_room(interaction)
        if channel is None:
            return
        await channel.set_permissions(interaction.guild.default_role, connect=False)
        await interaction.response.send_message("🔒 Room locked.", ephemeral=True)

    @room_group.command(name="unlock", description="Allow anyone to join your room again")
    async def unlock(self, interaction: discord.Interaction):
        channel = await self._get_owned_room(interaction)
        if channel is None:
            return
        await channel.set_permissions(interaction.guild.default_role, connect=True)
        await interaction.response.send_message("🔓 Room unlocked.", ephemeral=True)

    @room_group.command(name="rename", description="Rename your room")
    async def rename(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 100]):
        channel = await self._get_owned_room(interaction)
        if channel is None:
            return
        await channel.edit(name=name, reason=f"Renamed by owner {interaction.user}")
        await interaction.response.send_message(f"Room renamed to **{name}**.", ephemeral=True)

    @room_group.command(name="limit", description="Set the max number of members (0 = unlimited)")
    async def limit(self, interaction: discord.Interaction, count: app_commands.Range[int, 0, 99]):
        channel = await self._get_owned_room(interaction)
        if channel is None:
            return
        await channel.edit(user_limit=count, reason=f"Limit set by owner {interaction.user}")
        await interaction.response.send_message(f"User limit set to {'unlimited' if count == 0 else count}.", ephemeral=True)

    @room_group.command(name="kick", description="Kick a member out of your room")
    async def kick(self, interaction: discord.Interaction, member: discord.Member):
        channel = await self._get_owned_room(interaction)
        if channel is None:
            return
        if member.voice is None or member.voice.channel != channel:
            await interaction.response.send_message(f"{member.mention} isn't in your room.", ephemeral=True)
            return
        try:
            await member.move_to(None, reason=f"Kicked from voice room by owner {interaction.user}")
        except discord.Forbidden:
            pass
        await interaction.response.send_message(f"Kicked {member.mention} from the room.", ephemeral=True)

    @room_group.command(name="permit", description="Let a specific member join even while locked")
    async def permit(self, interaction: discord.Interaction, member: discord.Member):
        channel = await self._get_owned_room(interaction)
        if channel is None:
            return
        await channel.set_permissions(member, connect=True)
        await interaction.response.send_message(f"{member.mention} can now join even if the room is locked.", ephemeral=True)

    @room_group.command(name="block", description="Block a specific member from your room")
    async def block(self, interaction: discord.Interaction, member: discord.Member):
        channel = await self._get_owned_room(interaction)
        if channel is None:
            return
        await channel.set_permissions(member, connect=False)
        if member.voice is not None and member.voice.channel == channel:
            try:
                await member.move_to(None, reason=f"Blocked from voice room by owner {interaction.user}")
            except discord.Forbidden:
                pass
        await interaction.response.send_message(f"{member.mention} is now blocked from this room.", ephemeral=True)

    @room_group.command(name="transfer", description="Transfer room ownership to another member")
    async def transfer(self, interaction: discord.Interaction, member: discord.Member):
        channel = await self._get_owned_room(interaction)
        if channel is None:
            return
        if member.voice is None or member.voice.channel != channel:
            await interaction.response.send_message(f"{member.mention} needs to be in the room to receive ownership.", ephemeral=True)
            return
        await set_voice_room_owner(channel.id, member.id)
        await channel.set_permissions(
            member, connect=True, manage_channels=True, move_members=True, mute_members=True, deafen_members=True
        )
        await interaction.response.send_message(f"Ownership transferred to {member.mention}.", ephemeral=True)

    @room_group.command(name="claim", description="Claim ownership of this room if the owner has left")
    async def claim(self, interaction: discord.Interaction):
        member = interaction.user
        if member.voice is None or member.voice.channel is None:
            await interaction.response.send_message("You need to be in a voice room to use this.", ephemeral=True)
            return
        channel = member.voice.channel
        room = await get_voice_room(channel.id)
        if room is None:
            await interaction.response.send_message("This voice channel isn't a managed voice room.", ephemeral=True)
            return
        current_owner_still_here = any(m.id == room["owner_id"] for m in channel.members)
        if current_owner_still_here:
            await interaction.response.send_message("The current owner is still in the room.", ephemeral=True)
            return
        await set_voice_room_owner(channel.id, member.id)
        await channel.set_permissions(
            member, connect=True, manage_channels=True, move_members=True, mute_members=True, deafen_members=True
        )
        await interaction.response.send_message(f"{member.mention} is now the owner of this room.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceRooms(bot))
