"""
معالجة موحدة للأخطاء لأوامر الـ Slash (app_commands) -
بدل ما البوت "يطيح" بصمت لو صار خطأ، يرد برسالة واضحة للمستخدم ويسجل التفاصيل بالـ log.
"""

import logging

import discord
from discord import app_commands

logger = logging.getLogger("bot")


async def handle_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        message = "🚫 ما عندك صلاحية كافية تستخدم هذا الأمر."

    elif isinstance(error, app_commands.CheckFailure):
        # يشمل حالات فحوصات القنوات/الرولات المخصصة (انظر checks.py)
        message = "⚠️ ما تقدر تستخدم هذا الأمر هون (قناة أو صلاحية غير مسموحة)."

    else:
        logger.error(f"خطأ غير متوقع بأمر {interaction.command}: {error}", exc_info=True)
        message = "⚠️ صار خطأ غير متوقع أثناء تنفيذ الأمر. تم تسجيله للمراجعة."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


def setup_error_handling(bot):
    @bot.tree.error
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        await handle_app_command_error(interaction, error)
