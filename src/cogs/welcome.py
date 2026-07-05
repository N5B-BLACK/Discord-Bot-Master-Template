"""
نظام الترحيب: رسالة ترحيب مخصصة + رول تلقائي للأعضاء الجدد.
"""

import discord
from discord.ext import commands

import config


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # 1. إرسال رسالة الترحيب
        channel = None
        if config.WELCOME_CHANNEL_ID:
            channel = member.guild.get_channel(config.WELCOME_CHANNEL_ID)

        if channel:
            message = config.WELCOME_MESSAGE.format(
                member=member.mention, guild=member.guild.name
            )
            embed = discord.Embed(
                description=message,
                color=config.EMBED_COLOR,
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(embed=embed)

        # 2. إعطاء الرول التلقائي (إن وُجد)
        if config.AUTO_ROLE_ID:
            role = member.guild.get_role(config.AUTO_ROLE_ID)
            if role:
                await member.add_roles(role, reason="رول تلقائي للأعضاء الجدد")


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
