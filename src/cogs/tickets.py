"""
Ticket system:
- /ticket-panel: posts a panel with a persistent button in the current channel.
- When a member clicks it, a Private Thread opens visible to them + the support role only.
- Inside each ticket, 3 buttons for the support team: claim, summon (DM reminder), close.
- The support role is set via /setup (page 2).
- On close: a full transcript is posted to the log channel (if configured), and the
  thread is scheduled for automatic deletion 24 hours later.

Notes:
- Private Threads require the server to be Boost Level 2 or higher.
- Thread.owner_id returns the bot itself (since the bot technically created the thread
  via the API), not the real ticket opener - so we track the real opener manually in
  the database (utils/db.py).
"""

import datetime
import io

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.db import (
    create_ticket,
    get_guild_settings,
    get_next_ticket_number,
    get_ticket,
    get_tickets_due_for_deletion,
    mark_ticket_closed,
    mark_ticket_deleted,
    set_ticket_claim,
)
from utils.embed_helper import build_embed
from utils.log_helper import send_guild_log
from utils.message_templates import resolve_embed

OPEN_TICKET_CUSTOM_ID = "open_ticket_button"
CLAIM_TICKET_CUSTOM_ID = "claim_ticket_button"
SUMMON_TICKET_CUSTOM_ID = "summon_ticket_button"
CLOSE_TICKET_CUSTOM_ID = "close_ticket_button"


async def build_panel_embed(guild_id: int, guild_name: str = "") -> discord.Embed:
    """Shared builder for the ticket panel embed - used by both /ticket-panel and the dashboard."""

    async def default_embed():
        return await build_embed(
            guild_id,
            title="🎫 Support",
            description="Click the button below to open a private ticket, and the support team will assist you.",
        )

    return await resolve_embed(guild_id, "ticket_panel_embed_template", {"{guild}": guild_name}, default_embed)


async def _is_support(interaction: discord.Interaction) -> bool:
    settings = await get_guild_settings(interaction.guild_id)
    support_role_id = settings.get("ticket_support_role_id")
    return bool(support_role_id) and any(r.id == support_role_id for r in interaction.user.roles)


class CloseReasonModal(discord.ui.Modal, title="Close Ticket"):
    """Small popup shown on close - the reason field is optional, can be left blank."""

    reason = discord.ui.TextInput(
        label="Reason for closing (optional)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
        placeholder="Leave blank if you don't want to specify a reason",
    )

    async def on_submit(self, interaction: discord.Interaction):
        reason_text = self.reason.value.strip() if self.reason.value else None
        thread = interaction.channel

        # Generate a full transcript and send it to the log channel (if set) before scheduling deletion
        settings = await get_guild_settings(interaction.guild_id)
        log_channel_id = settings.get("ticket_log_channel_id")
        transcript_saved = False

        if log_channel_id:
            lines = []
            async for msg in thread.history(limit=None, oldest_first=True):
                stamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
                lines.append(f"[{stamp}] {msg.author}: {msg.content}")
            transcript_text = "\n".join(lines) or "No messages."
            file = discord.File(
                io.BytesIO(transcript_text.encode("utf-8")), filename=f"{thread.name}.txt"
            )
            log_embed = await build_embed(
                interaction.guild_id,
                title=f"📁 Closed ticket transcript - {thread.name}",
                color=discord.Color.dark_grey().value,
            )
            log_embed.timestamp = datetime.datetime.utcnow()
            log_embed.add_field(name="Closed by", value=interaction.user.mention, inline=True)
            if reason_text:
                log_embed.add_field(name="Reason", value=reason_text, inline=False)
            await send_guild_log(interaction.guild, "ticket_log_channel_id", log_embed, file=file)
            transcript_saved = True

        note = (
            "📁 A full transcript has been saved to the log channel."
            if transcript_saved
            else "⚠️ No log channel is configured, so no transcript was saved."
        )
        note += "\n🗑️ This channel will be automatically deleted in 24 hours."

        embed = await build_embed(
            interaction.guild_id, title="🔒 Ticket closed", color=discord.Color.red().value
        )
        embed.timestamp = datetime.datetime.utcnow()
        embed.add_field(name="Closed by", value=interaction.user.mention, inline=True)
        if reason_text:
            embed.add_field(name="Reason", value=reason_text, inline=False)
        embed.add_field(name="Note", value=note, inline=False)

        await interaction.response.send_message(embed=embed)

        # schedule auto-deletion in 24 hours (a background task checks periodically)
        delete_at = datetime.datetime.utcnow() + datetime.timedelta(hours=24)
        await mark_ticket_closed(thread.id, delete_at)

        # send the same closing info to the ticket opener via DM
        ticket = await get_ticket(thread.id)
        if ticket:
            opener = interaction.guild.get_member(ticket["opener_id"])
            if opener:
                dm_embed = await build_embed(
                    interaction.guild_id,
                    title="🔒 Your ticket was closed",
                    description=f"Your ticket in **{interaction.guild.name}** has been closed.",
                    color=discord.Color.red().value,
                )
                dm_embed.timestamp = datetime.datetime.utcnow()
                dm_embed.add_field(name="Closed by", value=str(interaction.user), inline=True)
                if reason_text:
                    dm_embed.add_field(name="Reason", value=reason_text, inline=False)
                dm_embed.add_field(name="Note", value=note, inline=False)
                try:
                    await opener.send(embed=dm_embed)
                except discord.Forbidden:
                    pass  # DMs closed - no need to block closing the ticket over this

        await thread.edit(archived=True, locked=True)


class TicketActionsView(discord.ui.View):
    """Buttons inside each ticket - persistent view (no timeout)."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🙋 Claim",
        style=discord.ButtonStyle.success,
        custom_id=CLAIM_TICKET_CUSTOM_ID,
    )
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await _is_support(interaction):
            await interaction.response.send_message("🚫 Only the support team can claim tickets.", ephemeral=True)
            return

        ticket = await get_ticket(interaction.channel.id)
        if ticket and ticket.get("claimed_by"):
            claimer = interaction.guild.get_member(ticket["claimed_by"])
            await interaction.response.send_message(
                f"⚠️ This ticket is already claimed by {claimer.mention if claimer else 'another member'}.",
                ephemeral=True,
            )
            return

        await set_ticket_claim(interaction.channel.id, interaction.user.id)
        await interaction.response.send_message(f"✅ {interaction.user.mention} claimed this ticket.")

    @discord.ui.button(
        label="📩 Summon",
        style=discord.ButtonStyle.primary,
        custom_id=SUMMON_TICKET_CUSTOM_ID,
    )
    async def summon_member(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await _is_support(interaction):
            await interaction.response.send_message("🚫 Only the support team can use this button.", ephemeral=True)
            return

        ticket = await get_ticket(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("⚠️ Couldn't find data for this ticket.", ephemeral=True)
            return

        opener = interaction.guild.get_member(ticket["opener_id"])
        if opener is None:
            await interaction.response.send_message("⚠️ Couldn't find the ticket opener in this server.", ephemeral=True)
            return

        try:
            await opener.send(
                f"👋 Reminder from the support team in **{interaction.guild.name}**: "
                f"there's a reply waiting for you in your ticket {interaction.channel.jump_url}"
            )
            await interaction.response.send_message(f"📩 Reminder DM sent to {opener.mention}.")
        except discord.Forbidden:
            await interaction.response.send_message(
                f"⚠️ Couldn't DM them (their DMs are closed). Try mentioning them here instead: {opener.mention}"
            )

    @discord.ui.button(
        label="🔒 Close Ticket",
        style=discord.ButtonStyle.danger,
        custom_id=CLOSE_TICKET_CUSTOM_ID,
    )
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = await get_ticket(interaction.channel.id)
        is_owner = ticket and ticket.get("opener_id") == interaction.user.id

        if not (await _is_support(interaction) or is_owner):
            await interaction.response.send_message(
                "🚫 Only the support team or the ticket owner can close it.", ephemeral=True
            )
            return

        await interaction.response.send_modal(CloseReasonModal())


class TicketPanelView(discord.ui.View):
    """Button to open a new ticket - persistent view shown on the support panel."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🎫 Open Ticket",
        style=discord.ButtonStyle.primary,
        custom_id=OPEN_TICKET_CUSTOM_ID,
    )
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await get_guild_settings(interaction.guild_id)
        support_role_id = settings.get("ticket_support_role_id")
        support_role = interaction.guild.get_role(support_role_id) if support_role_id else None

        if support_role is None:
            await interaction.response.send_message(
                "⚠️ No support role has been set yet. A server admin needs to configure it via `/setup`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        thread_name = f"ticket-{interaction.user.name}"[:100]
        try:
            thread = await interaction.channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.private_thread,
                invitable=False,
            )
        except discord.HTTPException:
            await interaction.followup.send(
                "❌ Couldn't create a private ticket thread. The server likely isn't at the "
                "Boost Level 2 required by Discord for private threads. Contact a server admin.",
                ephemeral=True,
            )
            return

        await thread.add_user(interaction.user)

        for member in support_role.members:
            try:
                await thread.add_user(member)
            except discord.HTTPException:
                pass

        # track the real ticket opener in the database (thread.owner_id would return the bot, not the user)
        await create_ticket(interaction.guild_id, thread.id, interaction.user.id)
        ticket_number = await get_next_ticket_number(interaction.guild_id)

        async def default_ticket_embed():
            e = await build_embed(
                interaction.guild_id,
                title="🎫 New Ticket",
                description="Describe your issue or request in detail, and the support team will be with you shortly.",
                color=discord.Color.green().value,
                use_brand_thumbnail=False,  # the requester's own avatar takes priority here
                timestamp=datetime.datetime.utcnow(),
            )
            e.set_thumbnail(url=interaction.user.display_avatar.url)
            e.add_field(name="👤 Ticket Owner", value=interaction.user.mention, inline=True)
            e.add_field(name="🛡️ Support Team", value=support_role.mention, inline=True)
            e.add_field(name="🔢 Ticket Number", value=f"#{ticket_number}", inline=True)
            e.set_footer(text="Opened on")
            return e

        embed = await resolve_embed(
            interaction.guild_id,
            "ticket_open_embed_template",
            {
                "{member}": interaction.user.display_name,
                "{member_mention}": interaction.user.mention,
                "{guild}": interaction.guild.name,
                "{ticket_number}": ticket_number,
                "{support_role_mention}": support_role.mention,
            },
            default_ticket_embed,
        )

        await thread.send(embed=embed, view=TicketActionsView())

        await interaction.followup.send(f"✅ Your ticket has been opened: {thread.mention}", ephemeral=True)


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cleanup_expired_tickets.start()

    def cog_unload(self):
        self.cleanup_expired_tickets.cancel()

    @tasks.loop(minutes=30)
    async def cleanup_expired_tickets(self):
        due = await get_tickets_due_for_deletion(datetime.datetime.utcnow())
        for ticket in due:
            channel = self.bot.get_channel(ticket["thread_id"])
            if channel:
                try:
                    await channel.delete()
                except discord.HTTPException:
                    pass
            await mark_ticket_deleted(ticket["thread_id"])

    @cleanup_expired_tickets.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="ticket-panel", description="Post the ticket panel in this channel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ticket_panel(self, interaction: discord.Interaction):
        embed = await build_panel_embed(interaction.guild_id, interaction.guild.name)
        await interaction.response.send_message(embed=embed, view=TicketPanelView())


async def setup(bot: commands.Bot):
    # register persistent views so the buttons keep working even after a restart
    bot.add_view(TicketPanelView())
    bot.add_view(TicketActionsView())
    await bot.add_cog(Tickets(bot))
