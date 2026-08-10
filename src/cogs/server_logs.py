"""
Extended server logs - the ProBot-style event types that go beyond the original set
(ban/unban, join/leave, message deletion, timeout, voice). Every event type has its
OWN dedicated log channel - deliberately not grouped (e.g. "channel created" and
"channel deleted" are two separate settings, not one "channel log") so a server can
route different event types to different channels, or mute the noisy ones without
losing the important ones.

- Message edited
- Messages bulk-deleted (a purge, not a single delete - message deletion cog handles
  single deletes; this handles the "someone cleared 50 messages" case)
- Channel created / deleted / updated (name, topic, slowmode) - 3 separate logs
- Role created / deleted / updated (name, color, permissions) - 3 separate logs
- Nickname changed / member roles changed - 2 separate logs
- Thread created / deleted / updated (archived, locked, renamed) - 3 separate logs

Where Discord's event doesn't include who made the change (channel/role/nickname/
thread edits don't come with an actor), the Audit Log is checked via the shared
get_recent_audit_entry() helper - same approach as ban/kick/timeout logging.
"""

import datetime

import discord
from discord.ext import commands

from utils.embed_helper import build_embed
from utils.log_helper import get_recent_audit_entry, send_guild_log


def _short(text: str, limit: int = 500) -> str:
    if not text:
        return "*(empty)*"
    return text if len(text) <= limit else text[: limit - 1] + "…"


class ServerLogs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -----------------------------------------------------------------
    # Message edited
    # -----------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.guild is None or after.author.bot:
            return
        if before.content == after.content:
            return  # embeds loading, pins, etc also fire this event with no real content change

        embed = await build_embed(
            before.guild.id,
            title="✏️ Message edited",
            color=discord.Color.blurple().value,
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="Author", value=after.author.mention, inline=True)
        embed.add_field(name="Channel", value=after.channel.mention, inline=True)
        if after.jump_url:
            embed.add_field(name="Jump to message", value=f"[Click here]({after.jump_url})", inline=True)
        embed.add_field(name="Before", value=_short(before.content), inline=False)
        embed.add_field(name="After", value=_short(after.content), inline=False)
        await send_guild_log(before.guild, "message_edit_log_channel_id", embed)

    # -----------------------------------------------------------------
    # Bulk message delete (a purge)
    # -----------------------------------------------------------------
    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]):
        if not messages or messages[0].guild is None:
            return
        guild = messages[0].guild
        channel = messages[0].channel

        entry = await get_recent_audit_entry(guild, discord.AuditLogAction.message_bulk_delete, channel.id)

        embed = await build_embed(
            guild.id,
            title="🧹 Messages bulk-deleted",
            color=discord.Color.dark_red().value,
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="Channel", value=channel.mention, inline=True)
        embed.add_field(name="Count", value=str(len(messages)), inline=True)
        if entry:
            embed.add_field(name="By", value=entry.user.mention, inline=True)
        await send_guild_log(guild, "message_bulk_delete_log_channel_id", embed)

    # -----------------------------------------------------------------
    # Channels: create / delete / update - 3 separate logs
    # -----------------------------------------------------------------
    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        entry = await get_recent_audit_entry(channel.guild, discord.AuditLogAction.channel_create, channel.id)
        embed = await build_embed(
            channel.guild.id,
            title="➕ Channel created",
            color=discord.Color.green().value,
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="Channel", value=f"{channel.mention} ({channel.type.name})", inline=True)
        if entry:
            embed.add_field(name="By", value=entry.user.mention, inline=True)
        await send_guild_log(channel.guild, "channel_create_log_channel_id", embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        entry = await get_recent_audit_entry(channel.guild, discord.AuditLogAction.channel_delete, channel.id)
        embed = await build_embed(
            channel.guild.id,
            title="➖ Channel deleted",
            color=discord.Color.dark_red().value,
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="Channel", value=f"#{channel.name} ({channel.type.name})", inline=True)
        if entry:
            embed.add_field(name="By", value=entry.user.mention, inline=True)
        await send_guild_log(channel.guild, "channel_delete_log_channel_id", embed)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        changes = []
        if before.name != after.name:
            changes.append(f"**Name:** {before.name} → {after.name}")
        before_topic = getattr(before, "topic", None)
        after_topic = getattr(after, "topic", None)
        if before_topic != after_topic:
            changes.append(f"**Topic:** {_short(before_topic, 100)} → {_short(after_topic, 100)}")
        before_slowmode = getattr(before, "slowmode_delay", None)
        after_slowmode = getattr(after, "slowmode_delay", None)
        if before_slowmode != after_slowmode:
            changes.append(f"**Slowmode:** {before_slowmode}s → {after_slowmode}s")
        if not changes:
            return  # permission overwrite changes, position changes etc - too noisy to log individually

        entry = await get_recent_audit_entry(after.guild, discord.AuditLogAction.channel_update, after.id)
        embed = await build_embed(
            after.guild.id,
            title="✏️ Channel updated",
            color=discord.Color.blurple().value,
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="Channel", value=after.mention if hasattr(after, "mention") else after.name, inline=True)
        if entry:
            embed.add_field(name="By", value=entry.user.mention, inline=True)
        embed.add_field(name="Changes", value="\n".join(changes), inline=False)
        await send_guild_log(after.guild, "channel_update_log_channel_id", embed)

    # -----------------------------------------------------------------
    # Roles: create / delete / update - 3 separate logs
    # -----------------------------------------------------------------
    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        entry = await get_recent_audit_entry(role.guild, discord.AuditLogAction.role_create, role.id)
        embed = await build_embed(
            role.guild.id,
            title="➕ Role created",
            color=discord.Color.green().value,
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="Role", value=role.mention, inline=True)
        if entry:
            embed.add_field(name="By", value=entry.user.mention, inline=True)
        await send_guild_log(role.guild, "role_create_log_channel_id", embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        entry = await get_recent_audit_entry(role.guild, discord.AuditLogAction.role_delete, role.id)
        embed = await build_embed(
            role.guild.id,
            title="➖ Role deleted",
            color=discord.Color.dark_red().value,
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="Role", value=f"@{role.name}", inline=True)
        if entry:
            embed.add_field(name="By", value=entry.user.mention, inline=True)
        await send_guild_log(role.guild, "role_delete_log_channel_id", embed)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        changes = []
        if before.name != after.name:
            changes.append(f"**Name:** {before.name} → {after.name}")
        if before.color != after.color:
            changes.append(f"**Color:** {before.color} → {after.color}")
        if before.permissions != after.permissions:
            changes.append("**Permissions changed**")
        if before.hoist != after.hoist:
            changes.append(f"**Displayed separately:** {before.hoist} → {after.hoist}")
        if not changes:
            return  # position-only changes are too noisy to log individually

        entry = await get_recent_audit_entry(after.guild, discord.AuditLogAction.role_update, after.id)
        embed = await build_embed(
            after.guild.id,
            title="✏️ Role updated",
            color=discord.Color.blurple().value,
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="Role", value=after.mention, inline=True)
        if entry:
            embed.add_field(name="By", value=entry.user.mention, inline=True)
        embed.add_field(name="Changes", value="\n".join(changes), inline=False)
        await send_guild_log(after.guild, "role_update_log_channel_id", embed)

    # -----------------------------------------------------------------
    # Member updated: nickname or roles changed - 2 separate logs
    # (timeout changes are handled separately in audit_logs.py - different log channel)
    # -----------------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.nick != after.nick:
            entry = await get_recent_audit_entry(after.guild, discord.AuditLogAction.member_update, after.id)
            embed = await build_embed(
                after.guild.id,
                title="✏️ Nickname changed",
                color=discord.Color.blurple().value,
                timestamp=datetime.datetime.utcnow(),
            )
            embed.add_field(name="Member", value=after.mention, inline=True)
            embed.add_field(name="Before", value=before.nick or "*(none)*", inline=True)
            embed.add_field(name="After", value=after.nick or "*(none)*", inline=True)
            if entry:
                embed.add_field(name="By", value=entry.user.mention, inline=True)
            await send_guild_log(after.guild, "nickname_change_log_channel_id", embed)

        before_roles = set(before.roles)
        after_roles = set(after.roles)
        if before_roles != after_roles:
            added = after_roles - before_roles
            removed = before_roles - after_roles
            entry = await get_recent_audit_entry(after.guild, discord.AuditLogAction.member_role_update, after.id)
            embed = await build_embed(
                after.guild.id,
                title="🎭 Member roles changed",
                color=discord.Color.blurple().value,
                timestamp=datetime.datetime.utcnow(),
            )
            embed.add_field(name="Member", value=after.mention, inline=True)
            if added:
                embed.add_field(name="Added", value=", ".join(r.mention for r in added), inline=False)
            if removed:
                embed.add_field(name="Removed", value=", ".join(r.mention for r in removed), inline=False)
            if entry:
                embed.add_field(name="By", value=entry.user.mention, inline=True)
            await send_guild_log(after.guild, "member_role_change_log_channel_id", embed)

    # -----------------------------------------------------------------
    # Threads: create / delete / update - 3 separate logs
    # -----------------------------------------------------------------
    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        entry = await get_recent_audit_entry(thread.guild, discord.AuditLogAction.thread_create, thread.id)
        embed = await build_embed(
            thread.guild.id,
            title="🧵 Thread created",
            color=discord.Color.green().value,
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="Thread", value=thread.mention, inline=True)
        if thread.parent:
            embed.add_field(name="In", value=thread.parent.mention, inline=True)
        if entry:
            embed.add_field(name="By", value=entry.user.mention, inline=True)
        elif thread.owner:
            embed.add_field(name="By", value=thread.owner.mention, inline=True)
        await send_guild_log(thread.guild, "thread_create_log_channel_id", embed)

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread):
        entry = await get_recent_audit_entry(thread.guild, discord.AuditLogAction.thread_delete, thread.id)
        embed = await build_embed(
            thread.guild.id,
            title="🧵 Thread deleted",
            color=discord.Color.dark_red().value,
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="Thread", value=f"#{thread.name}", inline=True)
        if thread.parent:
            embed.add_field(name="In", value=thread.parent.mention, inline=True)
        if entry:
            embed.add_field(name="By", value=entry.user.mention, inline=True)
        await send_guild_log(thread.guild, "thread_delete_log_channel_id", embed)

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        changes = []
        if before.name != after.name:
            changes.append(f"**Name:** {before.name} → {after.name}")
        if before.archived != after.archived:
            changes.append(f"**Archived:** {before.archived} → {after.archived}")
        if before.locked != after.locked:
            changes.append(f"**Locked:** {before.locked} → {after.locked}")
        if not changes:
            return

        entry = await get_recent_audit_entry(after.guild, discord.AuditLogAction.thread_update, after.id)
        embed = await build_embed(
            after.guild.id,
            title="🧵 Thread updated",
            color=discord.Color.blurple().value,
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="Thread", value=after.mention, inline=True)
        if entry:
            embed.add_field(name="By", value=entry.user.mention, inline=True)
        embed.add_field(name="Changes", value="\n".join(changes), inline=False)
        await send_guild_log(after.guild, "thread_update_log_channel_id", embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerLogs(bot))
