"""
معالجة موحدة للأخطاء - بدل ما البوت "يطيح" بصمت لو صار خطأ،
يرد برسالة واضحة للمستخدم ويسجل التفاصيل بالـ log.
"""

import logging

import discord
from discord.ext import commands

logger = logging.getLogger("bot")


async def handle_command_error(ctx: commands.Context, error: Exception):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("🚫 ما عندك صلاحية كافية تستخدم هذا الأمر.")

    elif isinstance(error, commands.CheckFailure):
        # يشمل حالات فحوصات القنوات/الرولات المخصصة (انظر checks.py)
        await ctx.send("⚠️ ما تقدر تستخدم هذا الأمر هون (قناة أو صلاحية غير مسموحة).")

    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❗ ناقص معطى مطلوب: `{error.param.name}`")

    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❗ ما لقيت هذا العضو بالسيرفر.")

    else:
        logger.error(f"خطأ غير متوقع بأمر {ctx.command}: {error}", exc_info=True)
        await ctx.send("⚠️ صار خطأ غير متوقع أثناء تنفيذ الأمر. تم تسجيله للمراجعة.")


def setup_error_handling(bot: commands.Bot):
    @bot.event
    async def on_command_error(ctx, error):
        await handle_command_error(ctx, error)
