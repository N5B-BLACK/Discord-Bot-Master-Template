"""
أوامر الموديريشن الأساسية: kick, ban, mute, warn.
كل الأوامر محمية بفحص رول المشرفين المحدد لكل سيرفر (عن طريق أمر /setup)، مش قيمة ثابتة بالـ .env.
"""

import datetime
import json
import os

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.checks import has_configured_role
from utils.db import get_guild_settings

WARNINGS_FILE = "warnings.json"


def _load_warnings() -> dict:
    if os.path.exists(WARNINGS_FILE):
        with open(WARNINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_warnings(data: dict):
    with open(WARNINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _log_action(self, guild: discord.Guild, author, action: str, member: discord.Member, reason: str):
        settings = await get_guild_settings(guild.id)
        log_channel_id = settings.get("mod_log_channel_id")
        if log_channel_id:
            channel = guild.get_channel(log_channel_id)
            if channel:
                embed = discord.Embed(
                    title=f"🔨 {action}",
                    color=config.EMBED_COLOR,
                    timestamp=datetime.datetime.utcnow(),
                )
                embed.add_field(name="العضو", value=f"{member} ({member.id})")
                embed.add_field(name="بواسطة", value=author.mention)
                embed.add_field(name="السبب", value=reason or "لم يُذكر", inline=False)
                await channel.send(embed=embed)

    @app_commands.command(name="kick", description="طرد عضو من السيرفر")
    @app_commands.describe(member="العضو المطلوب طرده", reason="سبب الطرد")
    @has_configured_role("mod_role_id")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = None):
        await member.kick(reason=reason)
        await interaction.response.send_message(f"👢 تم طرد {member.mention}. السبب: {reason or 'لم يُذكر'}")
        await self._log_action(interaction.guild, interaction.user, "Kick", member, reason)

    @app_commands.command(name="ban", description="حظر عضو من السيرفر")
    @app_commands.describe(member="العضو المطلوب حظره", reason="سبب الحظر")
    @has_configured_role("mod_role_id")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = None):
        await member.ban(reason=reason)
        await interaction.response.send_message(f"🔨 تم حظر {member.mention}. السبب: {reason or 'لم يُذكر'}")
        await self._log_action(interaction.guild, interaction.user, "Ban", member, reason)

    @app_commands.command(name="mute", description="إسكات عضو مؤقتاً")
    @app_commands.describe(member="العضو المطلوب إسكاته", minutes="عدد الدقائق", reason="السبب")
    @has_configured_role("mod_role_id")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mute(self, interaction: discord.Interaction, member: discord.Member, minutes: int = 10, reason: str = None):
        duration = datetime.timedelta(minutes=minutes)
        await member.timeout(duration, reason=reason)
        await interaction.response.send_message(f"🔇 تم إسكات {member.mention} لمدة {minutes} دقيقة.")
        await self._log_action(interaction.guild, interaction.user, f"Mute ({minutes} min)", member, reason)

    @app_commands.command(name="warn", description="تحذير عضو")
    @app_commands.describe(member="العضو المطلوب تحذيره", reason="سبب التحذير")
    @has_configured_role("mod_role_id")
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str = None):
        data = _load_warnings()
        guild_data = data.setdefault(str(interaction.guild.id), {})
        member_warnings = guild_data.setdefault(str(member.id), [])
        member_warnings.append(
            {"reason": reason or "لم يُذكر", "by": interaction.user.id, "date": str(datetime.date.today())}
        )
        _save_warnings(data)

        await interaction.response.send_message(
            f"⚠️ تم تحذير {member.mention}. عدد التحذيرات الآن: {len(member_warnings)}"
        )
        await self._log_action(interaction.guild, interaction.user, "Warn", member, reason)

    @app_commands.command(name="warnings", description="عرض تحذيرات عضو")
    @app_commands.describe(member="العضو المطلوب عرض تحذيراته")
    @has_configured_role("mod_role_id")
    async def list_warnings(self, interaction: discord.Interaction, member: discord.Member):
        data = _load_warnings()
        member_warnings = data.get(str(interaction.guild.id), {}).get(str(member.id), [])
        if not member_warnings:
            await interaction.response.send_message(f"✅ {member.mention} ما عنده أي تحذيرات.")
            return

        embed = discord.Embed(title=f"تحذيرات {member}", color=config.EMBED_COLOR)
        for i, w in enumerate(member_warnings, start=1):
            embed.add_field(name=f"#{i} - {w['date']}", value=w["reason"], inline=False)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
