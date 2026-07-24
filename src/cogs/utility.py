"""
أوامر عامة بسيطة: ping، info، say.
"""

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.checks import has_configured_role


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

    @app_commands.command(name="say", description="يخلي البوت يكتب رسالة بقناة محددة")
    @app_commands.describe(channel="القناة المطلوب الإرسال فيها", message="نص الرسالة")
    @has_configured_role("mod_role_id")
    async def say(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        message: str,
    ):
        try:
            await channel.send(message)
        except discord.Forbidden:
            await interaction.response.send_message(
                f"🚫 ما عندي صلاحية أكتب بقناة {channel.mention}."
            )
            return

        await interaction.response.send_message(
            f"✅ تم إرسال الرسالة بقناة {channel.mention}."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Utility(bot))
