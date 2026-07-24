"""
نظام التذاكر (Tickets):
- /ticket-panel: ينشر لوحة فيها زر ثابت بالقناة الحالية.
- لما عضو يضغط الزر، ينفتحله Private Thread يشوفه هو + رول الدعم بس.
- /setup-tickets: يحدد رول الدعم المسؤول عن التذاكر (منفصل عن /setup الرئيسي
  لأن اللوحة الأساسية أصلاً معبّية 5 صفوف - الحد الأقصى المسموح بديسكورد).

ملاحظة مهمة: Private Threads بتحتاج السيرفر يكون Boost Level 2 فأعلى.
لو السيرفر أقل من هيك، محاولة فتح تذكرة بترجع خطأ واضح للمستخدم بدل ما "تطيح" بصمت.
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils.db import get_guild_settings, update_guild_setting

OPEN_TICKET_CUSTOM_ID = "open_ticket_button"
CLOSE_TICKET_CUSTOM_ID = "close_ticket_button"


class CloseTicketView(discord.ui.View):
    """زر إغلاق داخل كل تذكرة - View دائم (بدون انتهاء صلاحية)."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🔒 إغلاق التذكرة",
        style=discord.ButtonStyle.danger,
        custom_id=CLOSE_TICKET_CUSTOM_ID,
    )
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await get_guild_settings(interaction.guild_id)
        support_role_id = settings.get("ticket_support_role_id")
        is_support = support_role_id and any(r.id == support_role_id for r in interaction.user.roles)

        thread = interaction.channel
        is_owner = getattr(thread, "owner_id", None) == interaction.user.id

        if not (is_support or is_owner):
            await interaction.response.send_message(
                "🚫 بس فريق الدعم أو صاحب التذكرة يقدر يسكرها.", ephemeral=True
            )
            return

        await interaction.response.send_message("🔒 جاري إغلاق التذكرة...")
        await thread.edit(archived=True, locked=True)


class TicketPanelView(discord.ui.View):
    """زر فتح تذكرة جديدة - View دائم يظهر بلوحة الدعم."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🎫 افتح تذكرة",
        style=discord.ButtonStyle.primary,
        custom_id=OPEN_TICKET_CUSTOM_ID,
    )
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await get_guild_settings(interaction.guild_id)
        support_role_id = settings.get("ticket_support_role_id")
        support_role = interaction.guild.get_role(support_role_id) if support_role_id else None

        if support_role is None:
            await interaction.response.send_message(
                "⚠️ ما في رول دعم محدد بعد. لازم أدمن السيرفر يضبطه أول عن طريق `/setup-tickets`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        thread_name = f"ticket-{interaction.user.name}"[:100]
        try:
            thread = await interaction.channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.private_thread,
                invitable=False,
            )
        except discord.HTTPException:
            await interaction.followup.send(
                "❌ ما قدرت أفتح تذكرة خاصة. غالبًا السيرفر مش وصل مستوى Boost 2 "
                "المطلوب من ديسكورد لإنشاء Threads خاصة. خبر أدمن السيرفر.",
                ephemeral=True,
            )
            return

        await thread.add_user(interaction.user)

        for member in support_role.members:
            try:
                await thread.add_user(member)
            except discord.HTTPException:
                pass

        embed = discord.Embed(
            title="🎫 تذكرة جديدة",
            description=(
                f"أهلاً {interaction.user.mention}! فريق {support_role.mention} رح يتواصل معك هون قريبًا.\n\n"
                "اشرح مشكلتك أو طلبك بالتفصيل بأول رسالة."
            ),
            color=discord.Color.green(),
        )
        await thread.send(embed=embed, view=CloseTicketView())

        await interaction.followup.send(f"✅ تم فتح تذكرتك: {thread.mention}", ephemeral=True)


class TicketSupportRoleView(discord.ui.View):
    """View بسيط لتحديد رول الدعم بس - منفصل عن /setup الرئيسي."""

    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="اختر رول الدعم المسؤول عن التذاكر",
        row=0,
    )
    async def support_role_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        role = select.values[0]
        await update_guild_setting(interaction.guild_id, "ticket_support_role_id", role.id)
        await interaction.response.send_message(
            f"✅ تم تحديد رول الدعم: {role.mention}", ephemeral=True
        )


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ticket-panel", description="ينشر لوحة فتح التذاكر بهاي القناة")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ticket_panel(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎫 الدعم الفني",
            description="اضغط الزر تحت لفتح تذكرة خاصة، وفريق الدعم رح يتواصل معك.",
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, view=TicketPanelView())

    @app_commands.command(name="setup-tickets", description="حدد رول الدعم المسؤول عن التذاكر")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_tickets(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "اختر رول الدعم:", view=TicketSupportRoleView(), ephemeral=True
        )


async def setup(bot: commands.Bot):
    # تسجيل الـ Views الدائمة عشان الأزرار تضل شغالة حتى بعد إعادة تشغيل البوت
    bot.add_view(TicketPanelView())
    bot.add_view(CloseTicketView())
    await bot.add_cog(Tickets(bot))
