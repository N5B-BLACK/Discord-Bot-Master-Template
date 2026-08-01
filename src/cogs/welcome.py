"""
Welcome event for new members - sends a branded welcome embed and assigns an auto-role
based on each server's settings (configured via /setup or the dashboard).

Bots and humans get different auto-roles (bot_auto_role_id vs auto_role_id) since
servers often want a distinct "Bots" role separate from the regular member role.
"""

import discord
from discord.ext import commands

import config
from utils.db import get_guild_settings
from utils.embed_helper import build_embed
from utils.message_templates import resolve_embed


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        settings = await get_guild_settings(member.guild.id)

        # Welcome message only makes sense for humans
        if not member.bot:
            welcome_channel_id = settings.get("welcome_channel_id")
            if welcome_channel_id:
                channel = member.guild.get_channel(welcome_channel_id)
                if channel:
                    async def default_embed():
                        message = config.WELCOME_MESSAGE.format(member=member.mention, guild=member.guild.name)
                        embed = await build_embed(
                            member.guild.id,
                            title="🎉 Welcome!",
                            description=message,
                            use_brand_thumbnail=False,
                        )
                        embed.set_thumbnail(url=member.display_avatar.url)
                        return embed

                    embed = await resolve_embed(
                        member.guild.id,
                        "welcome_embed_template",
                        {
                            "{member}": member.display_name,
                            "{member_mention}": member.mention,
                            "{guild}": member.guild.name,
                            "{member_count}": member.guild.member_count,
                        },
                        default_embed,
                    )
                    await channel.send(embed=embed)

        # Auto-role: separate role for bots vs humans
        role_key = "bot_auto_role_id" if member.bot else "auto_role_id"
        role_id = settings.get(role_key)
        if role_id:
            role = member.guild.get_role(role_id)
            if role:
                await member.add_roles(role)


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
