"""
/setup command - lets a server admin configure everything (roles and channels) themselves
via interactive select menus, without typing any IDs manually or touching code.
Every choice is saved instantly to the database (MongoDB) for this server only.

Each page's embed lists the current value of every setting on that page by number
(e.g. "1️⃣ Mod role: @Staff"). This matters because Discord replaces a select menu's
placeholder text with the chosen item's name once a default value is set - so without
this summary in the embed, you'd only see a channel/role name with no label telling you
which setting it belongs to.

Discord hard-limits a message to 5 action rows, and a select menu always takes a full
row by itself - so every page here caps at 4 selects + 1 row of Back/Next buttons.
Grouped by topic (mirrors the dashboard's Server Settings page grouping exactly - keep
both in sync if a setting is ever added or moved):
- Page 1: Core moderation (mod role, warn log, ban/unban log, kick log)
- Page 2: Members & welcome (welcome channel, auto-role for members, auto-role for
          bots, server join/leave log)
- Page 3: AI & tickets (AI channel, support role, ticket log)
- Page 4: Voice logs (join/leave, switch, disconnect, mute)
- Page 5: More logs (deafen, message deletion, timeout, settings-update log)
"""

import datetime

import discord
from discord import app_commands
from discord.ext import commands

from utils.db import get_guild_settings, update_guild_setting
from utils.log_helper import send_guild_log


async def _apply_setting(interaction: discord.Interaction, key: str, value_id: int, label: str, mention: str):
    """Saves the setting, replies to the admin, and logs it to the update-log channel if configured."""
    await update_guild_setting(interaction.guild_id, key, value_id)
    await interaction.response.send_message(f"✅ {label} set to: {mention}", ephemeral=True)

    log_embed = discord.Embed(
        title="⚙️ Setting updated",
        color=discord.Color.blurple(),
        timestamp=datetime.datetime.utcnow(),
    )
    log_embed.add_field(name="Setting", value=label, inline=True)
    log_embed.add_field(name="New value", value=mention, inline=True)
    log_embed.add_field(name="By", value=interaction.user.mention, inline=True)
    await send_guild_log(interaction.guild, "setup_update_log_channel_id", log_embed)


def _mention(settings: dict, key: str, kind: str) -> str:
    """Builds a raw Discord mention string from a stored ID, without needing to fetch the object.
    kind is 'channel' or 'role'. Returns '*Not set*' if the setting is empty."""
    value = settings.get(key)
    if not value:
        return "*Not set*"
    return f"<#{value}>" if kind == "channel" else f"<@&{value}>"


def _default_values(settings: dict, key: str):
    value = settings.get(key)
    return [discord.Object(id=value)] if value else []


def _page1_embed(settings: dict) -> discord.Embed:
    description = (
        "Pick from the menus below to configure each setting. Current values are listed here - "
        "anything showing *Not set* stays unrestricted.\n\n"
        "**Core moderation**\n"
        f"1️⃣ Mod role: {_mention(settings, 'mod_role_id', 'role')}\n"
        f"2️⃣ Warn log channel: {_mention(settings, 'warn_log_channel_id', 'channel')}\n"
        f"3️⃣ Ban/unban log channel: {_mention(settings, 'ban_unban_log_channel_id', 'channel')}\n"
        f"4️⃣ Kick log channel: {_mention(settings, 'kicked_log_channel_id', 'channel')}\n\n"
        "Click **Next ▶️** for more settings."
    )
    return discord.Embed(title="⚙️ Bot Settings (1/5) · Core Moderation", description=description, color=discord.Color.blurple())


def _page2_embed(settings: dict) -> discord.Embed:
    description = (
        "**Members & welcome**\n\n"
        f"5️⃣ Welcome channel: {_mention(settings, 'welcome_channel_id', 'channel')}\n"
        f"6️⃣ Auto-role (new members): {_mention(settings, 'auto_role_id', 'role')}\n"
        f"7️⃣ Auto-role (new bots): {_mention(settings, 'bot_auto_role_id', 'role')}\n"
        f"8️⃣ Server join/leave log: {_mention(settings, 'server_join_leave_log_channel_id', 'channel')}\n\n"
        "Humans and bots get separate auto-roles since they usually need different permissions."
    )
    return discord.Embed(title="⚙️ Bot Settings (2/5) · Members & Welcome", description=description, color=discord.Color.blurple())


def _page3_embed(settings: dict) -> discord.Embed:
    description = (
        "**AI & tickets**\n\n"
        f"9️⃣ AI channel (/ask): {_mention(settings, 'ai_chat_channel_id', 'channel')}\n"
        f"🔟 Support role (tickets): {_mention(settings, 'ticket_support_role_id', 'role')}\n"
        f"1️⃣1️⃣ Ticket log channel: {_mention(settings, 'ticket_log_channel_id', 'channel')}\n\n"
        "To post the ticket panel itself (the \"🎫 Open Ticket\" button), use the dashboard's "
        "Ticket Panel page or `/ticket-panel` - that's an action, not a setting, so it isn't here."
    )
    return discord.Embed(title="⚙️ Bot Settings (3/5) · AI & Tickets", description=description, color=discord.Color.blurple())


def _page4_embed(settings: dict) -> discord.Embed:
    description = (
        "**Voice logs** - each event type goes to its own channel.\n"
        "⚠️ 'Disconnect' needs the bot to have **View Audit Log** permission.\n\n"
        f"1️⃣2️⃣ Voice join/leave: {_mention(settings, 'voice_join_leave_log_channel_id', 'channel')}\n"
        f"1️⃣3️⃣ Voice switch: {_mention(settings, 'voice_switch_log_channel_id', 'channel')}\n"
        f"1️⃣4️⃣ Voice disconnect: {_mention(settings, 'voice_disconnect_log_channel_id', 'channel')}\n"
        f"1️⃣5️⃣ Voice mute/unmute: {_mention(settings, 'voice_mute_log_channel_id', 'channel')}"
    )
    return discord.Embed(title="⚙️ Bot Settings (4/5) · Voice Logs", description=description, color=discord.Color.blurple())


def _page5_embed(settings: dict) -> discord.Embed:
    description = (
        "**More logs**\n"
        "⚠️ Some of these need **View Audit Log** permission to identify who's responsible.\n\n"
        f"1️⃣6️⃣ Voice deafen/undeafen log: {_mention(settings, 'voice_deafen_log_channel_id', 'channel')}\n"
        f"1️⃣7️⃣ Message deletion log: {_mention(settings, 'msg_deleted_log_channel_id', 'channel')}\n"
        f"1️⃣8️⃣ Timeout log: {_mention(settings, 'timeout_log_channel_id', 'channel')}\n"
        f"1️⃣9️⃣ Settings-update log: {_mention(settings, 'setup_update_log_channel_id', 'channel')}\n\n"
        "The settings-update log records every change made on this page (and the dashboard) - who changed what, and when."
    )
    return discord.Embed(title="⚙️ Bot Settings (5/5) · More Logs", description=description, color=discord.Color.blurple())


class SetupViewPage1(discord.ui.View):
    def __init__(self, settings: dict):
        super().__init__(timeout=300)
        self.mod_role_select.default_values = _default_values(settings, "mod_role_id")
        self.warn_log_select.default_values = _default_values(settings, "warn_log_channel_id")
        self.ban_unban_select.default_values = _default_values(settings, "ban_unban_log_channel_id")
        self.kicked_select.default_values = _default_values(settings, "kicked_log_channel_id")

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="1️⃣ Select the mod role", row=0)
    async def mod_role_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        role = select.values[0]
        await _apply_setting(interaction, "mod_role_id", role.id, "Mod role", role.mention)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="2️⃣ Select the warn log channel",
        channel_types=[discord.ChannelType.text],
        row=1,
    )
    async def warn_log_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel = select.values[0]
        await _apply_setting(interaction, "warn_log_channel_id", channel.id, "Warn log channel", channel.mention)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="3️⃣ Select the ban/unban log channel",
        channel_types=[discord.ChannelType.text],
        row=2,
    )
    async def ban_unban_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel = select.values[0]
        await _apply_setting(interaction, "ban_unban_log_channel_id", channel.id, "Ban/unban log", channel.mention)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="4️⃣ Select the kick log channel",
        channel_types=[discord.ChannelType.text],
        row=3,
    )
    async def kicked_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel = select.values[0]
        await _apply_setting(interaction, "kicked_log_channel_id", channel.id, "Kick log", channel.mention)

    @discord.ui.button(label="Next ▶️", style=discord.ButtonStyle.secondary, row=4)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await get_guild_settings(interaction.guild_id)
        await interaction.response.edit_message(embed=_page2_embed(settings), view=SetupViewPage2(settings))


class SetupViewPage2(discord.ui.View):
    def __init__(self, settings: dict):
        super().__init__(timeout=300)
        self.welcome_channel_select.default_values = _default_values(settings, "welcome_channel_id")
        self.auto_role_select.default_values = _default_values(settings, "auto_role_id")
        self.bot_auto_role_select.default_values = _default_values(settings, "bot_auto_role_id")
        self.server_join_leave_select.default_values = _default_values(settings, "server_join_leave_log_channel_id")

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="5️⃣ Select the welcome channel",
        channel_types=[discord.ChannelType.text],
        row=0,
    )
    async def welcome_channel_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel = select.values[0]
        await _apply_setting(interaction, "welcome_channel_id", channel.id, "Welcome channel", channel.mention)

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="6️⃣ Select the auto-role for new members", row=1)
    async def auto_role_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        role = select.values[0]
        await _apply_setting(interaction, "auto_role_id", role.id, "Auto-role (members)", role.mention)

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="7️⃣ Select the auto-role for new bots", row=2)
    async def bot_auto_role_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        role = select.values[0]
        await _apply_setting(interaction, "bot_auto_role_id", role.id, "Auto-role (bots)", role.mention)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="8️⃣ Select the server join/leave log channel",
        channel_types=[discord.ChannelType.text],
        row=3,
    )
    async def server_join_leave_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel = select.values[0]
        await _apply_setting(
            interaction, "server_join_leave_log_channel_id", channel.id, "Server join/leave log", channel.mention
        )

    @discord.ui.button(label="◀️ Back", style=discord.ButtonStyle.secondary, row=4)
    async def back_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await get_guild_settings(interaction.guild_id)
        await interaction.response.edit_message(embed=_page1_embed(settings), view=SetupViewPage1(settings))

    @discord.ui.button(label="Next ▶️", style=discord.ButtonStyle.secondary, row=4)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await get_guild_settings(interaction.guild_id)
        await interaction.response.edit_message(embed=_page3_embed(settings), view=SetupViewPage3(settings))


class SetupViewPage3(discord.ui.View):
    def __init__(self, settings: dict):
        super().__init__(timeout=300)
        self.ai_channel_select.default_values = _default_values(settings, "ai_chat_channel_id")
        self.ticket_role_select.default_values = _default_values(settings, "ticket_support_role_id")
        self.ticket_log_select.default_values = _default_values(settings, "ticket_log_channel_id")

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="9️⃣ Select the /ask (AI) channel",
        channel_types=[discord.ChannelType.text],
        row=0,
    )
    async def ai_channel_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel = select.values[0]
        await _apply_setting(interaction, "ai_chat_channel_id", channel.id, "AI channel", channel.mention)

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="🔟 Select the support role (tickets)", row=1)
    async def ticket_role_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        role = select.values[0]
        await _apply_setting(interaction, "ticket_support_role_id", role.id, "Support role", role.mention)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="1️⃣1️⃣ Select the ticket log channel (closed ticket transcripts)",
        channel_types=[discord.ChannelType.text],
        row=2,
    )
    async def ticket_log_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel = select.values[0]
        await _apply_setting(interaction, "ticket_log_channel_id", channel.id, "Ticket log channel", channel.mention)

    @discord.ui.button(label="◀️ Back", style=discord.ButtonStyle.secondary, row=3)
    async def back_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await get_guild_settings(interaction.guild_id)
        await interaction.response.edit_message(embed=_page2_embed(settings), view=SetupViewPage2(settings))

    @discord.ui.button(label="Next ▶️", style=discord.ButtonStyle.secondary, row=3)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await get_guild_settings(interaction.guild_id)
        await interaction.response.edit_message(embed=_page4_embed(settings), view=SetupViewPage4(settings))


class SetupViewPage4(discord.ui.View):
    def __init__(self, settings: dict):
        super().__init__(timeout=300)
        self.voice_join_leave_select.default_values = _default_values(settings, "voice_join_leave_log_channel_id")
        self.voice_switch_select.default_values = _default_values(settings, "voice_switch_log_channel_id")
        self.voice_disconnect_select.default_values = _default_values(settings, "voice_disconnect_log_channel_id")
        self.voice_mute_select.default_values = _default_values(settings, "voice_mute_log_channel_id")

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="1️⃣2️⃣ Voice join/leave log channel",
        channel_types=[discord.ChannelType.text],
        row=0,
    )
    async def voice_join_leave_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel = select.values[0]
        await _apply_setting(
            interaction, "voice_join_leave_log_channel_id", channel.id, "Voice join/leave log", channel.mention
        )

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="1️⃣3️⃣ Voice switch log channel",
        channel_types=[discord.ChannelType.text],
        row=1,
    )
    async def voice_switch_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel = select.values[0]
        await _apply_setting(
            interaction, "voice_switch_log_channel_id", channel.id, "Voice switch log", channel.mention
        )

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="1️⃣4️⃣ Voice disconnect log channel",
        channel_types=[discord.ChannelType.text],
        row=2,
    )
    async def voice_disconnect_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel = select.values[0]
        await _apply_setting(
            interaction, "voice_disconnect_log_channel_id", channel.id, "Voice disconnect log", channel.mention
        )

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="1️⃣5️⃣ Voice mute/unmute log channel",
        channel_types=[discord.ChannelType.text],
        row=3,
    )
    async def voice_mute_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel = select.values[0]
        await _apply_setting(interaction, "voice_mute_log_channel_id", channel.id, "Voice mute log", channel.mention)

    @discord.ui.button(label="◀️ Back", style=discord.ButtonStyle.secondary, row=4)
    async def back_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await get_guild_settings(interaction.guild_id)
        await interaction.response.edit_message(embed=_page3_embed(settings), view=SetupViewPage3(settings))

    @discord.ui.button(label="Next ▶️", style=discord.ButtonStyle.secondary, row=4)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await get_guild_settings(interaction.guild_id)
        await interaction.response.edit_message(embed=_page5_embed(settings), view=SetupViewPage5(settings))


class SetupViewPage5(discord.ui.View):
    def __init__(self, settings: dict):
        super().__init__(timeout=300)
        self.voice_deafen_select.default_values = _default_values(settings, "voice_deafen_log_channel_id")
        self.msg_deleted_select.default_values = _default_values(settings, "msg_deleted_log_channel_id")
        self.timeout_select.default_values = _default_values(settings, "timeout_log_channel_id")
        self.setup_update_select.default_values = _default_values(settings, "setup_update_log_channel_id")

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="1️⃣6️⃣ Voice deafen/undeafen log channel",
        channel_types=[discord.ChannelType.text],
        row=0,
    )
    async def voice_deafen_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel = select.values[0]
        await _apply_setting(
            interaction, "voice_deafen_log_channel_id", channel.id, "Voice deafen log", channel.mention
        )

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="1️⃣7️⃣ Message deletion log channel",
        channel_types=[discord.ChannelType.text],
        row=1,
    )
    async def msg_deleted_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel = select.values[0]
        await _apply_setting(interaction, "msg_deleted_log_channel_id", channel.id, "Message deletion log", channel.mention)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="1️⃣8️⃣ Timeout log channel",
        channel_types=[discord.ChannelType.text],
        row=2,
    )
    async def timeout_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel = select.values[0]
        await _apply_setting(interaction, "timeout_log_channel_id", channel.id, "Timeout log", channel.mention)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="1️⃣9️⃣ Settings-update log channel",
        channel_types=[discord.ChannelType.text],
        row=3,
    )
    async def setup_update_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel = select.values[0]
        await _apply_setting(
            interaction, "setup_update_log_channel_id", channel.id, "Settings-update log", channel.mention
        )

    @discord.ui.button(label="◀️ Back", style=discord.ButtonStyle.secondary, row=4)
    async def back_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await get_guild_settings(interaction.guild_id)
        await interaction.response.edit_message(embed=_page4_embed(settings), view=SetupViewPage4(settings))


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setup", description="Configure the bot's settings for this server")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_command(self, interaction: discord.Interaction):
        settings = await get_guild_settings(interaction.guild_id)
        await interaction.response.send_message(
            embed=_page1_embed(settings), view=SetupViewPage1(settings), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))
