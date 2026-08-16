"""
Leveling (Phase 2) - message-based XP, /rank rank cards, /leaderboard, and
automatic role rewards at configured levels.

XP is awarded per eligible message (not per character/word - that's trivially
gameable) with a per-member cooldown so nobody can farm levels by spamming.
The curve itself lives in utils/leveling_math.py; the rank card image in
utils/rank_card.py. This file is just the Discord-facing wiring: the listener
that awards XP, the level-up announcement + role rewards, and the commands.
"""

import random
import time
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

from utils.db import (
    DEFAULT_SETTINGS,
    add_xp,
    get_guild_settings,
    get_leaderboard,
    get_rank_position,
    get_user_level,
    set_level_role,
    set_leveling_setting,
)
from utils.embed_helper import build_embed
from utils.leveling_math import level_from_xp, progress_in_level
from utils.licensing import is_module_available
from utils.rank_card import generate_rank_card


def _accent_color(settings: dict) -> tuple[int, int, int]:
    color_int = settings.get("embed_color") or 0x5865F2
    return ((color_int >> 16) & 0xFF, (color_int >> 8) & 0xFF, color_int & 0xFF)


class Leveling(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # guild_id -> member_id -> last XP-earning timestamp, for the cooldown
        self._last_xp_at: dict[int, dict[int, float]] = defaultdict(dict)

    leveling_group = app_commands.Group(name="leveling", description="Configure the leveling system")

    # -----------------------------------------------------------------
    # XP awarding
    # -----------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        settings = await get_guild_settings(message.guild.id)
        conf = settings.get("leveling", {})
        if not conf.get("enabled") or not is_module_available(settings, "leveling"):
            return
        if message.channel.id in conf.get("ignored_channel_ids", []):
            return

        cooldown = conf.get("cooldown_seconds", 60)
        now = time.time()
        last = self._last_xp_at[message.guild.id].get(message.author.id, 0)
        if now - last < cooldown:
            return
        self._last_xp_at[message.guild.id][message.author.id] = now

        gained = random.randint(conf.get("xp_min", 15), conf.get("xp_max", 25))
        current = await get_user_level(message.guild.id, message.author.id)
        old_level = current.get("level", level_from_xp(current.get("xp", 0)))
        new_total = current.get("xp", 0) + gained
        new_level = level_from_xp(new_total)
        await add_xp(message.guild.id, message.author.id, gained, new_level)

        if new_level > old_level:
            await self._handle_level_up(message, settings, conf, new_level)

    async def _handle_level_up(self, message: discord.Message, settings: dict, conf: dict, new_level: int) -> None:
        # Role rewards - assign every configured level_role at or below the new level
        # that the member doesn't already have (covers multi-level jumps in one message).
        level_roles = conf.get("level_roles", {})
        to_add = []
        for level_str, role_id in level_roles.items():
            if int(level_str) <= new_level:
                role = message.guild.get_role(role_id)
                if role and role not in message.author.roles:
                    to_add.append(role)
        if to_add:
            try:
                await message.author.add_roles(*to_add, reason="Leveling: level role reward")
            except discord.Forbidden:
                pass

        announce_channel_id = conf.get("announce_channel_id")
        channel = message.guild.get_channel(announce_channel_id) if announce_channel_id else message.channel
        if channel is None:
            return
        text = conf.get(
            "announce_message", DEFAULT_SETTINGS["leveling"]["announce_message"]
        ).format(member_mention=message.author.mention, level=new_level)
        try:
            await channel.send(text)
        except discord.Forbidden:
            pass

    # -----------------------------------------------------------------
    # /rank, /leaderboard
    # -----------------------------------------------------------------
    @app_commands.command(name="rank", description="Show your (or someone else's) rank card")
    async def rank(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        await interaction.response.defer()

        doc = await get_user_level(interaction.guild_id, member.id)
        xp = doc.get("xp", 0)
        level, xp_into_level, xp_needed = progress_in_level(xp)
        rank_pos = await get_rank_position(interaction.guild_id, member.id, xp)

        settings = await get_guild_settings(interaction.guild_id)
        buf = await generate_rank_card(
            display_name=member.display_name,
            avatar_url=member.display_avatar.replace(size=256, static_format="png").url,
            level=level,
            rank=rank_pos,
            xp_into_level=xp_into_level,
            xp_needed=xp_needed,
            accent_color=_accent_color(settings),
        )
        await interaction.followup.send(file=discord.File(buf, filename="rank.png"))

    @app_commands.command(name="leaderboard", description="Show the top members by XP")
    async def leaderboard(self, interaction: discord.Interaction):
        rows = await get_leaderboard(interaction.guild_id, limit=10)
        if not rows:
            await interaction.response.send_message("No one has earned any XP yet.", ephemeral=True)
            return

        lines = []
        for i, row in enumerate(rows, start=1):
            member = interaction.guild.get_member(row["member_id"])
            name = member.display_name if member else f"Unknown ({row['member_id']})"
            level = row.get("level", level_from_xp(row.get("xp", 0)))
            lines.append(f"**{i}.** {name} — Level {level} ({row.get('xp', 0):,} XP)")

        embed = await build_embed(interaction.guild_id, title="🏆 Leaderboard", description="\n".join(lines))
        await interaction.response.send_message(embed=embed)

    # -----------------------------------------------------------------
    # /leveling config commands - full configuration also available from the
    # dashboard's upcoming Leveling page (next increment, same as Security's).
    # -----------------------------------------------------------------
    @leveling_group.command(name="toggle", description="Turn the leveling system on or off")
    @app_commands.checks.has_permissions(administrator=True)
    async def toggle(self, interaction: discord.Interaction, enabled: bool):
        await set_leveling_setting(interaction.guild_id, "enabled", enabled)
        await interaction.response.send_message(f"Leveling {'enabled ✅' if enabled else 'disabled ❌'}.", ephemeral=True)

    @leveling_group.command(name="xp-range", description="Set the min/max XP awarded per eligible message")
    @app_commands.checks.has_permissions(administrator=True)
    async def xp_range(self, interaction: discord.Interaction, minimum: app_commands.Range[int, 1, 1000], maximum: app_commands.Range[int, 1, 1000]):
        if minimum > maximum:
            minimum, maximum = maximum, minimum
        await set_leveling_setting(interaction.guild_id, "xp_min", minimum)
        await set_leveling_setting(interaction.guild_id, "xp_max", maximum)
        await interaction.response.send_message(f"XP per message set to {minimum}-{maximum}.", ephemeral=True)

    @leveling_group.command(name="cooldown", description="Set the XP cooldown in seconds")
    @app_commands.checks.has_permissions(administrator=True)
    async def cooldown(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 1, 3600]):
        await set_leveling_setting(interaction.guild_id, "cooldown_seconds", seconds)
        await interaction.response.send_message(f"XP cooldown set to {seconds}s.", ephemeral=True)

    @leveling_group.command(name="announce-channel", description="Set where level-up messages are sent")
    @app_commands.checks.has_permissions(administrator=True)
    async def announce_channel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        await set_leveling_setting(interaction.guild_id, "announce_channel_id", channel.id if channel else None)
        msg = f"Level-ups will be announced in {channel.mention}." if channel else "Level-ups will be announced in the channel where they happen."
        await interaction.response.send_message(msg, ephemeral=True)

    @leveling_group.command(name="level-role", description="Award a role automatically at a level (omit role to remove)")
    @app_commands.checks.has_permissions(administrator=True)
    async def level_role(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 1000], role: discord.Role = None):
        await set_level_role(interaction.guild_id, level, role.id if role else None)
        msg = f"Level {level} will now award {role.mention}." if role else f"Removed the role reward for level {level}."
        await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Leveling(bot))
