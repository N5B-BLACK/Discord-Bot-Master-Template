"""
حدث الترحيب بالأعضاء الجدد - يرسل رسالة ترحيب ويعطي رول تلقائي حسب إعدادات كل سيرفر
(مضبوطة عن طريق أمر /setup).
"""

import discord
from discord.ext import commands

import config
from utils.db import get_guild_settings


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        settings = await get_guild_settings(member.guild.id)

        welcome_channel_id = settings.get("welcome_channel_id")
        if welcome_channel_id:
            channel = member.guild.get_channel(welcome_channel_id)
            if channel:
                message = config.WELCOME_MESSAGE.format(member=member.mention, guild=member.guild.name)
                await channel.send(message)

        auto_role_id = settings.get("auto_role_id")
        if auto_role_id:
            role = member.guild.get_role(auto_role_id)
            if role:
                await member.add_roles(role)


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
