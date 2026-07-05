"""
أوامر الموديريشن الأساسية: kick, ban, timeout, warn.
كل الأوامر محمية بفحص الرول (MOD_ROLE_ID من config).
"""

import datetime
import json
import os

import discord
from discord.ext import commands

import config
from utils.checks import has_role_id

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

    async def _log_action(self, ctx, action: str, member: discord.Member, reason: str):
        if config.MOD_LOG_CHANNEL_ID:
            channel = ctx.guild.get_channel(config.MOD_LOG_CHANNEL_ID)
            if channel:
                embed = discord.Embed(
                    title=f"🔨 {action}",
                    color=config.EMBED_COLOR,
                    timestamp=datetime.datetime.utcnow(),
                )
                embed.add_field(name="العضو", value=f"{member} ({member.id})")
                embed.add_field(name="بواسطة", value=ctx.author.mention)
                embed.add_field(name="السبب", value=reason or "لم يُذكر", inline=False)
                await channel.send(embed=embed)

    @commands.command(name="kick")
    @has_role_id(config.MOD_ROLE_ID)
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx, member: discord.Member, *, reason: str = None):
        await member.kick(reason=reason)
        await ctx.send(f"👢 تم طرد {member.mention}. السبب: {reason or 'لم يُذكر'}")
        await self._log_action(ctx, "Kick", member, reason)

    @commands.command(name="ban")
    @has_role_id(config.MOD_ROLE_ID)
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx, member: discord.Member, *, reason: str = None):
        await member.ban(reason=reason)
        await ctx.send(f"🔨 تم حظر {member.mention}. السبب: {reason or 'لم يُذكر'}")
        await self._log_action(ctx, "Ban", member, reason)

    @commands.command(name="mute")
    @has_role_id(config.MOD_ROLE_ID)
    @commands.has_permissions(moderate_members=True)
    async def mute(self, ctx, member: discord.Member, minutes: int = 10, *, reason: str = None):
        duration = datetime.timedelta(minutes=minutes)
        await member.timeout(duration, reason=reason)
        await ctx.send(f"🔇 تم إسكات {member.mention} لمدة {minutes} دقيقة.")
        await self._log_action(ctx, f"Mute ({minutes} min)", member, reason)

    @commands.command(name="warn")
    @has_role_id(config.MOD_ROLE_ID)
    async def warn(self, ctx, member: discord.Member, *, reason: str = None):
        data = _load_warnings()
        guild_data = data.setdefault(str(ctx.guild.id), {})
        member_warnings = guild_data.setdefault(str(member.id), [])
        member_warnings.append(
            {"reason": reason or "لم يُذكر", "by": ctx.author.id, "date": str(datetime.date.today())}
        )
        _save_warnings(data)

        await ctx.send(
            f"⚠️ تم تحذير {member.mention}. عدد التحذيرات الآن: {len(member_warnings)}"
        )
        await self._log_action(ctx, "Warn", member, reason)

    @commands.command(name="warnings")
    @has_role_id(config.MOD_ROLE_ID)
    async def list_warnings(self, ctx, member: discord.Member):
        data = _load_warnings()
        member_warnings = data.get(str(ctx.guild.id), {}).get(str(member.id), [])
        if not member_warnings:
            await ctx.send(f"✅ {member.mention} ما عنده أي تحذيرات.")
            return

        embed = discord.Embed(title=f"تحذيرات {member}", color=config.EMBED_COLOR)
        for i, w in enumerate(member_warnings, start=1):
            embed.add_field(name=f"#{i} - {w['date']}", value=w["reason"], inline=False)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
