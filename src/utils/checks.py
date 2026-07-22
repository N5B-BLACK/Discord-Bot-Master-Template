"""
فحوصات (checks) قابلة لإعادة الاستخدام: تقييد أمر بقناة معينة أو رول معين.
تُستخدم مع أوامر الـ Slash (app_commands).
"""

from discord import app_commands, Interaction


def in_channel(channel_id: int | None):
    """يسمح بتنفيذ الأمر فقط داخل القناة المحددة. لو channel_id=None، ما في تقييد."""

    def predicate(interaction: Interaction) -> bool:
        if channel_id is None:
            return True
        return interaction.channel_id == channel_id

    return app_commands.check(predicate)


def has_role_id(role_id: int | None):
    """يسمح بتنفيذ الأمر فقط لمن يملك الرول المحدد. لو role_id=None، يُسمح للجميع."""

    def predicate(interaction: Interaction) -> bool:
        if role_id is None:
            return True
        return any(role.id == role_id for role in interaction.user.roles)

    return app_commands.check(predicate)
