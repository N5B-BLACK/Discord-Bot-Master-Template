"""
نظام التذاكر (Tickets):
- /ticket-panel: ينشر لوحة فيها زر ثابت بالقناة الحالية.
- لما عضو يضغط الزر، ينفتحله Private Thread يشوفه هو + رول الدعم بس.
- داخل كل تذكرة 3 أزرار لفريق الدعم: استلام، استحضار (تذكير بالخاص)، إغلاق.
- رول الدعم يتحدد عن طريق /setup (صفحة 2).

ملاحظات مهمة:
- Private Threads بتحتاج السيرفر يكون Boost Level 2 فأعلى.
- Thread.owner_id بيرجع البوت نفسه (لأنه هو يلي أنشأ الـ Thread تقنيًا)، مش صاحب
  التذكرة الحقيقي - لهيك بنسجل صاحب التذكرة يدويًا بقاعدة البيانات (utils/db.py).
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils.db import create_ticket, get_guild_settings, get_ticket, set_ticket_claim

OPEN_TICKET_CUSTOM_ID = "open_ticket_button"
CLAIM_TICKET_CUSTOM_ID = "claim_ticket_button"
SUMMON_TICKET_CUSTOM_ID = "summon_ticket_button"
CLOSE_TICKET_CUSTOM_ID = "close_ticket_button"


async def _is_support(interaction: discord.Interaction) -> bool:
    settings = await get_guild_settings(interaction.guild_id)
    support_role_id = settings.get("ticket_support_role_id")
    return bool(support_role_id) and any(r.id == support_role_id for r in interaction.user.roles)


class TicketActionsView(discord.ui.View):
    """أزرار داخل كل تذكرة - View دائم (بدون انتهاء صلاحية)."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🙋 استلام",
        style=discord.ButtonStyle.success,
        custom_id=CLAIM_TICKET_CUSTOM_ID,
    )
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await _is_support(interaction):
            await interaction.response.send_message("🚫 بس فريق الدعم يقدر يستلم التذاكر.", ephemeral=True)
            return

        ticket = await get_ticket(interaction.channel.id)
        if ticket and ticket.get("claimed_by"):
            claimer = interaction.guild.get_member(ticket["claimed_by"])
            await interaction.response.send_message(
                f"⚠️ التذكرة مستلمة أصلاً من {claimer.mention if claimer else 'عضو آخر'}.",
                ephemeral=True,
            )
            return

        await set_ticket_claim(interaction.channel.id, interaction.user.id)
        await interaction.response.send_message(f"✅ {interaction.user.mention} استلم هاي التذكرة.")

    @discord.ui.button(
        label="📩 استحضار",
        style=discord.ButtonStyle.primary,
        custom_id=SUMMON_TICKET_CUSTOM_ID,
    )
    async def summon_member(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await _is_support(interaction):
            await interaction.response.send_message("🚫 بس فريق الدعم يقدر يستخدم هاد الزر.", ephemeral=True)
            return

        ticket = await get_ticket(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("⚠️ ما لقيت بيانات هاي التذكرة.", ephemeral=True)
            return

        opener = interaction.guild.get_member(ticket["opener_id"])
        if opener is None:
            await interaction.response.send_message("⚠️ ما قدرت ألاقي صاحب التذكرة بالسيرفر.", ephemeral=True)
            return

        try:
            await opener.send(
                f"👋 تذكير من فريق الدعم بسيرفر **{interaction.guild.name}**: "
                f"في رد بانتظارك بتذكرتك {interaction.channel.jump_url}"
            )
            await interaction.response.send_message(f"📩 تم إرسال تذكير لـ {opener.mention} على الخاص.")
        except discord.Forbidden:
            await interaction.response.send_message(
                f"⚠️ ما قدرت أبعتله رسالة خاصة (الخاص مسكر عنده). جرب تمنشنه هون مباشرة: {opener.mention}"
            )

    @discord.ui.button(
        label="🔒 إغلاق التذكرة",
        style=discord.ButtonStyle.danger,
        custom_id=CLOSE_TICKET_CUSTOM_ID,
    )
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = await get_ticket(interaction.channel.id)
        is_owner = ticket and ticket.get("opener_id") == interaction.user.id

        if not (await _is_support(interaction) or is_owner):
            await interaction.response.send_message(
                "🚫 بس فريق الدعم أو صاحب التذكرة يقدر يسكرها.", ephemeral=True
            )
            return

        await interaction.response.send_message("🔒 جاري إغلاق التذكرة...")
        await interaction.channel.edit(archived=True, locked=True)


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
                "⚠️ ما في رول دعم محدد بعد. لازم أدمن السيرفر يضبطه أول عن طريق `/setup`.",
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

        # نسجل صاحب التذكرة الحقيقي بقاعدة البيانات (thread.owner_id بيرجع البوت مش العضو)
        await create_ticket(interaction.guild_id, thread.id, interaction.user.id)

        embed = discord.Embed(
            title="🎫 تذكرة جديدة",
            description=(
                f"أهلاً {interaction.user.mention}! فريق {support_role.mention} رح يتواصل معك هون قريبًا.\n\n"
                "اشرح مشكلتك أو طلبك بالتفصيل بأول رسالة."
            ),
            color=discord.Color.green(),
        )
        await thread.send(embed=embed, view=TicketActionsView())

        await interaction.followup.send(f"✅ تم فتح تذكرتك: {thread.mention}", ephemeral=True)


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


async def setup(bot: commands.Bot):
    # تسجيل الـ Views الدائمة عشان الأزرار تضل شغالة حتى بعد إعادة تشغيل البوت
    bot.add_view(TicketPanelView())
    bot.add_view(TicketActionsView())
    await bot.add_cog(Tickets(bot))
