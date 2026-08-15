"""
Stats collection (Phase 4) - the minimal set of counters the Overview page's
charts need: messages/day, joins/day, leaves/day, and a daily member-count
snapshot. Deliberately just increments in-memory-cheap counters per event
rather than storing every message/join individually - the charts only ever
need daily totals, so there's no reason to pay for per-event storage.

The daily snapshot task also acts as a safety net for the member-count trend:
even on a day with zero joins/leaves, the guild's member count still gets
recorded, so the growth chart has a real data point for every day rather than
gaps.
"""

import discord
from discord.ext import commands, tasks

from utils.db import increment_daily_stat, set_member_count_snapshot


class Stats(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.snapshot_task.start()

    def cog_unload(self):
        self.snapshot_task.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        await increment_daily_stat(message.guild.id, "messages")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await increment_daily_stat(member.guild.id, "joins")
        await set_member_count_snapshot(member.guild.id, member.guild.member_count)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await increment_daily_stat(member.guild.id, "leaves")
        await set_member_count_snapshot(member.guild.id, member.guild.member_count)

    @tasks.loop(hours=1)
    async def snapshot_task(self):
        for guild in self.bot.guilds:
            await set_member_count_snapshot(guild.id, guild.member_count)

    @snapshot_task.before_loop
    async def before_snapshot_task(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Stats(bot))
