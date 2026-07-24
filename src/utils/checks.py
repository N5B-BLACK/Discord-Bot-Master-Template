"""
فحوصات (checks) قابلة لإعادة الاستخدام - تعتمد على إعدادات كل سيرفر المخزنة بقاعدة البيانات
(مضبوطة عن طريق أمر /setup)، مش على قيم ثابتة بالـ .env.
"""

from discord import app_commands, Interaction

from utils.db import get_guild_settings


def in_configured_channel(setting_key: str):
    """
    يسمح بالتنفيذ فقط بالقناة المحفوظة تحت setting_key لهذا السيرفر.
    لو ما ضبط الأدمن قناة بعد (عن طريق /setup)، يسمح بأي قناة.
    """

    async def predicate(interaction: Interaction) -> bool:
        settings = await get_guild_settings(interaction.guild_id)
        channel_id = settings.get(setting_key)
        if channel_id is None:
            return True
        return interaction.channel_id == channel_id

    return app_commands.check(predicate)


def has_configured_role(setting_key: str):
    """
    يسمح بالتنفيذ فقط لمن يملك الرول المحفوظ تحت setting_key لهذا السيرفر.
    لو ما ضبط الأدمن رول بعد (عن طريق /setup)، يسمح للجميع.
    """

    async def predicate(interaction: Interaction) -> bool:
        settings = await get_guild_settings(interaction.guild_id)
        role_id = settings.get(setting_key)
        if role_id is None:
            return True
        return any(role.id == role_id for role in interaction.user.roles)

    return app_commands.check(predicate)
