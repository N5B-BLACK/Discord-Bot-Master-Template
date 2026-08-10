"""
/setup command - lets a server admin configure everything (roles and channels) themselves
via interactive select menus, without typing any IDs manually or touching code.
Every choice is saved instantly to the database (MongoDB) for this server only, and
logged to the settings-update log channel (shared with dashboard edits - see
utils/log_helper.py's log_setting_change).

Data-driven pagination: SETTINGS_PAGES is the single source of truth for what's on
each page - add or remove a (key, label, kind) tuple there and the page embed, the
selects, and the Back/Next buttons all follow automatically. No per-page classes to
hand-maintain. Discord hard-limits a message to 5 action rows, and a select menu
always takes a full row by itself, so every page caps at 4 selects + 1 row of
Back/Next buttons - SETTINGS_PAGES is pre-split into groups of at most 4 accordingly.

Keep this in sync with dashboard.py's SETTINGS_GROUPS and utils/db.py's
DEFAULT_SETTINGS if a setting is ever added, renamed, or removed - all three must
agree on the exact same set of keys.
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils.db import get_guild_settings, update_guild_setting
from utils.log_helper import log_setting_change

NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

# (page title, [(key, label, kind), ...]) - kind is "role" or "channel". Max 4 items/page.
SETTINGS_PAGES = [
    ("Core Moderation", [
        ("mod_role_id", "Mod Role", "role"),
        ("warn_log_channel_id", "Warn Log Channel", "channel"),
        ("ban_unban_log_channel_id", "Ban/Unban Log", "channel"),
        ("kicked_log_channel_id", "Kick Log", "channel"),
    ]),
    ("Members & Welcome", [
        ("welcome_channel_id", "Welcome Channel", "channel"),
        ("auto_role_id", "Auto-Role (new members)", "role"),
        ("bot_auto_role_id", "Auto-Role (new bots)", "role"),
        ("server_join_leave_log_channel_id", "Server Join/Leave Log", "channel"),
    ]),
    ("AI, Tickets & Security", [
        ("ai_chat_channel_id", "AI Channel (/ask)", "channel"),
        ("ticket_support_role_id", "Support Role", "role"),
        ("ticket_log_channel_id", "Ticket Log Channel", "channel"),
        ("trap_channel_id", "Trap Channel (auto-ban on post)", "channel"),
    ]),
    ("Voice Logs (1/2)", [
        ("voice_join_leave_log_channel_id", "Join/Leave Log", "channel"),
        ("voice_switch_log_channel_id", "Switch Log (self)", "channel"),
        ("voice_move_log_channel_id", "Moved Log (by a mod)", "channel"),
        ("voice_disconnect_log_channel_id", "Disconnect Log", "channel"),
    ]),
    ("Voice Logs (2/2) & Moderation Logs", [
        ("voice_mute_log_channel_id", "Mute/Unmute Log", "channel"),
        ("voice_deafen_log_channel_id", "Deafen/Undeafen Log", "channel"),
        ("msg_deleted_log_channel_id", "Message Deletion Log", "channel"),
        ("timeout_log_channel_id", "Timeout Log", "channel"),
    ]),
    ("Message & Settings Logs", [
        ("message_edit_log_channel_id", "Message Edit Log", "channel"),
        ("message_bulk_delete_log_channel_id", "Bulk Delete Log", "channel"),
        ("setup_update_log_channel_id", "Settings-Update Log", "channel"),
        ("channel_create_log_channel_id", "Channel Created Log", "channel"),
    ]),
    ("Channel & Role Logs", [
        ("channel_delete_log_channel_id", "Channel Deleted Log", "channel"),
        ("channel_update_log_channel_id", "Channel Updated Log", "channel"),
        ("role_create_log_channel_id", "Role Created Log", "channel"),
        ("role_delete_log_channel_id", "Role Deleted Log", "channel"),
    ]),
    ("Role & Member Logs", [
        ("role_update_log_channel_id", "Role Updated Log", "channel"),
        ("nickname_change_log_channel_id", "Nickname Change Log", "channel"),
        ("member_role_change_log_channel_id", "Member Role Change Log", "channel"),
        ("thread_create_log_channel_id", "Thread Created Log", "channel"),
    ]),
    ("Thread Logs", [
        ("thread_delete_log_channel_id", "Thread Deleted Log", "channel"),
        ("thread_update_log_channel_id", "Thread Updated Log", "channel"),
    ]),
]


def _mention(settings: dict, key: str, kind: str) -> str:
    value = settings.get(key)
    if not value:
        return "*Not set*"
    return f"<#{value}>" if kind == "channel" else f"<@&{value}>"


def _default_values(settings: dict, key: str):
    value = settings.get(key)
    return [discord.Object(id=value)] if value else []


def _page_embed(page_index: int, settings: dict) -> discord.Embed:
    title, fields = SETTINGS_PAGES[page_index]
    total_pages = len(SETTINGS_PAGES)
    lines = [
        f"{NUM_EMOJI[i]} {label}: {_mention(settings, key, kind)}" for i, (key, label, kind) in enumerate(fields)
    ]
    description = "\n".join(lines)
    if page_index == 0:
        description = (
            "Pick from the menus below to configure each setting. Current values are listed here - "
            "anything showing *Not set* stays unrestricted.\n\n" + description
        )
    if page_index < total_pages - 1:
        description += "\n\nClick **Next ▶️** for more settings."
    return discord.Embed(
        title=f"⚙️ Bot Settings ({page_index + 1}/{total_pages}) · {title}",
        description=description,
        color=discord.Color.blurple(),
    )


class _RoleSettingSelect(discord.ui.RoleSelect):
    def __init__(self, key: str, label: str, row: int, settings: dict):
        super().__init__(placeholder=f"Select: {label}", row=row, default_values=_default_values(settings, key))
        self.key = key
        self.label_text = label

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        await update_guild_setting(interaction.guild_id, self.key, role.id)
        await interaction.response.send_message(f"✅ {self.label_text} set to: {role.mention}", ephemeral=True)
        await log_setting_change(interaction.guild, self.label_text, role.mention, interaction.user.mention)


class _ChannelSettingSelect(discord.ui.ChannelSelect):
    def __init__(self, key: str, label: str, row: int, settings: dict):
        super().__init__(
            placeholder=f"Select: {label}",
            channel_types=[discord.ChannelType.text],
            row=row,
            default_values=_default_values(settings, key),
        )
        self.key = key
        self.label_text = label

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]
        await update_guild_setting(interaction.guild_id, self.key, channel.id)
        await interaction.response.send_message(f"✅ {self.label_text} set to: {channel.mention}", ephemeral=True)
        await log_setting_change(interaction.guild, self.label_text, channel.mention, interaction.user.mention)


class SetupPageView(discord.ui.View):
    def __init__(self, page_index: int, settings: dict):
        super().__init__(timeout=300)
        self.page_index = page_index
        _, fields = SETTINGS_PAGES[page_index]

        for i, (key, label, kind) in enumerate(fields):
            if kind == "role":
                self.add_item(_RoleSettingSelect(key, label, i, settings))
            else:
                self.add_item(_ChannelSettingSelect(key, label, i, settings))

        nav_row = 4
        if page_index > 0:
            back = discord.ui.Button(label="◀️ Back", style=discord.ButtonStyle.secondary, row=nav_row)
            back.callback = self._nav_callback(page_index - 1)
            self.add_item(back)
        if page_index < len(SETTINGS_PAGES) - 1:
            nxt = discord.ui.Button(label="Next ▶️", style=discord.ButtonStyle.secondary, row=nav_row)
            nxt.callback = self._nav_callback(page_index + 1)
            self.add_item(nxt)

    def _nav_callback(self, target_page: int):
        async def callback(interaction: discord.Interaction):
            settings = await get_guild_settings(interaction.guild_id)
            await interaction.response.edit_message(
                embed=_page_embed(target_page, settings), view=SetupPageView(target_page, settings)
            )

        return callback


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setup", description="Configure the bot's settings for this server")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_command(self, interaction: discord.Interaction):
        settings = await get_guild_settings(interaction.guild_id)
        await interaction.response.send_message(
            embed=_page_embed(0, settings), view=SetupPageView(0, settings), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))
