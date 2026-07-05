"""
فحوصات (checks) قابلة لإعادة الاستخدام: تقييد أمر بقناة معينة أو رول معين.
استخدمها كـ decorator فوق أي أمر.
"""

from discord.ext import commands


def in_channel(channel_id: int | None):
    """يسمح بتنفيذ الأمر فقط داخل القناة المحددة. لو channel_id=None، ما في تقييد."""

    async def predicate(ctx: commands.Context) -> bool:
        if channel_id is None:
            return True
        return ctx.channel.id == channel_id

    return commands.check(predicate)


def has_role_id(role_id: int | None):
    """يسمح بتنفيذ الأمر فقط لمن يملك الرول المحدد. لو role_id=None، يُسمح للجميع."""

    async def predicate(ctx: commands.Context) -> bool:
        if role_id is None:
            return True
        return any(role.id == role_id for role in ctx.author.roles)

    return commands.check(predicate)
