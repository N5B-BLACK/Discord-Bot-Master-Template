"""
أوامر عامة بسيطة: ping، info.
"""

import discord
from discord import app_commands
from discord.ext import commands

import config


class Utility(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ping", description="فحص استجابة البوت")
    async def ping(self, interaction: discord.Interaction):
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"🏓 Pong! ({latency_ms}ms)")

    @app_commands.command(name="info", description="معلومات عن البوت")
    async def info(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title=config.BOT_NAME,
            description="بوت مخصص مبني على Master Template.",
            color=config.EMBED_COLOR,
        )
        embed.add_field(name="السيرفرات", value=str(len(self.bot.guilds)))
        embed.add_field(name="الأوامر المتاحة", value="استخدم `/` لعرض كل الأوامر")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Utility(bot))
