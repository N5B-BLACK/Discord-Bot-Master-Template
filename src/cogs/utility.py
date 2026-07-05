"""
أوامر عامة بسيطة: ping، info، help مخصص.
"""

import discord
from discord.ext import commands

import config


class Utility(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="ping")
    async def ping(self, ctx: commands.Context):
        latency_ms = round(self.bot.latency * 1000)
        await ctx.send(f"🏓 Pong! ({latency_ms}ms)")

    @commands.command(name="info")
    async def info(self, ctx: commands.Context):
        embed = discord.Embed(
            title=config.BOT_NAME,
            description="بوت مخصص مبني على Master Template.",
            color=config.EMBED_COLOR,
        )
        embed.add_field(name="السيرفرات", value=str(len(self.bot.guilds)))
        embed.add_field(name="الأوامر المتاحة", value=f"استخدم `{config.PREFIX}help`")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Utility(bot))
