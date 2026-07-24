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

import datetime
import io

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.db import (
    create_ticket,
    get_guild_settings,
    get_next_ticket_number,
    get_ticket,
    get_tickets_due_for_deletion,
    mark_ticket_closed,
    mark_ticket_deleted,
    set_ticket_claim,
)

OPEN_TICKET_CUSTOM_ID = "open_ticket_button"
CLAIM_TICKET_CUSTOM_ID = "claim_ticket_button"
SUMMON_TICKET_CUSTOM_ID = "summon_ticket_button"
CLOSE_TICKET_CUSTOM_ID = "close_ticket_button"


async def _is_support(interaction: discord.Interaction) -> bool:
    settings = await get_guild_settings(interaction.guild_id)
    support_role_id = settings.get("ticket_support_role_id")
    return bool(support_role_id) and any(r.id == support_role_id for r in interaction.user.roles)


class CloseReasonModal(discord.ui.Modal, title="إغلاق التذكرة"):
    """نافذة صغيرة تظهر وقت الإغلاق - خانة السبب اختيارية، ممكن تخليها فاضية."""

    reason = discord.ui.TextInput(
        label="سبب الإغلاق (اختياري)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
        placeholder="اتركها فاضية لو ما بدك تحدد سبب",
    )

    async def on_submit(self, interaction: discord.Interaction):
        reason_text = self.reason.value.strip() if self.reason.value else None
        thread = interaction.channel

        # نولّد نسخة كاملة من المحادثة ونبعتها لقناة اللوج (لو محددة) قبل ما نجدول الحذف
        settings = await get_guild_settings(interaction.guild_id)
        log_channel_id = settings.get("ticket_log_channel_id")
        log_channel = interaction.guild.get_channel(log_channel_id) if log_channel_id else None
        transcript_saved = False

        if log_channel:
            lines = []
            async for msg in thread.history(limit=None, oldest_first=True):
                stamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
                lines.append(f"[{stamp}] {msg.author}: {msg.content}")
            transcript_text = "\n".join(lines) or "لا توجد رسائل."
            file = discord.File(
                io.BytesIO(transcript_text.encode("utf-8")), filename=f"{thread.name}.txt"
            )
            log_embed = discord.Embed(
                title=f"📁 نسخة تذكرة مغلقة - {thread.name}",
                color=discord.Color.dark_grey(),
                timestamp=datetime.datetime.utcnow(),
            )
            log_embed.add_field(name="بواسطة", value=interaction.user.mention, inline=True)
            if reason_text:
                log_embed.add_field(name="السبب", value=reason_text, inline=False)
            await log_channel.send(embed=log_embed, file=file)
            transcript_saved = True

        note = (
            "📁 تم حفظ نسخة كاملة من المحادثة بقناة اللوج."
            if transcript_saved
            else "⚠️ ما في قناة لوج محددة، فما راح تنحفظ نسخة من المحادثة."
        )
        note += "\n🗑️ هاي القناة رح تنحذف تلقائيًا خلال 24 ساعة."

        embed = discord.Embed(
            title="🔒 تم إغلاق التذكرة",
            color=discord.Color.red(),
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="بواسطة", value=interaction.user.mention, inline=True)
        if reason_text:
            embed.add_field(name="السبب", value=reason_text, inline=False)
        embed.add_field(name="ملاحظة", value=note, inline=False)

        await interaction.response.send_message(embed=embed)

        # نجدول الحذف التلقائي بعد 24 ساعة (مهمة الخلفية بتتأكد كل شوي مين وصل وقته)
        delete_at = datetime.datetime.utcnow() + datetime.timedelta(hours=24)
        await mark_ticket_closed(thread.id, delete_at)

        # نبعت نفس معلومات الإغلاق لصاحب التذكرة عالخاص
        ticket = await get_ticket(thread.id)
        if ticket:
            opener = interaction.guild.get_member(ticket["opener_id"])
            if opener:
                dm_embed = discord.Embed(
                    title="🔒 تم إغلاق تذكرتك",
                    description=f"تذكرتك بسيرفر **{interaction.guild.name}** انسكرت.",
                    color=discord.Color.red(),
                    timestamp=datetime.datetime.utcnow(),
                )
                dm_embed.add_field(name="بواسطة", value=str(interaction.user), inline=True)
                if reason_text:
                    dm_embed.add_field(name="السبب", value=reason_text, inline=False)
                dm_embed.add_field(name="ملاحظة", value=note, inline=False)
                try:
                    await opener.send(embed=dm_embed)
                except discord.Forbidden:
                    pass  # مسكر الـ DMs - ما في داعي نوقف عملية الإغلاق بسبب هيك

        await thread.edit(archived=True, locked=True)


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

        await interaction.response.send_modal(CloseReasonModal())


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
        ticket_number = await get_next_ticket_number(interaction.guild_id)

        embed = discord.Embed(
            title="🎫 تذكرة جديدة",
            description="اشرح مشكلتك أو طلبك بالتفصيل، وفريق الدعم رح يتواصل معك قريبًا.",
            color=discord.Color.green(),
            timestamp=datetime.datetime.utcnow(),
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name="👤 صاحب التذكرة", value=interaction.user.mention, inline=True)
        embed.add_field(name="🛡️ فريق الدعم", value=support_role.mention, inline=True)
        embed.add_field(name="🔢 رقم التذكرة", value=f"#{ticket_number}", inline=True)
        embed.set_footer(text="تاريخ الفتح")

        await thread.send(embed=embed, view=TicketActionsView())

        await interaction.followup.send(f"✅ تم فتح تذكرتك: {thread.mention}", ephemeral=True)


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cleanup_expired_tickets.start()

    def cog_unload(self):
        self.cleanup_expired_tickets.cancel()

    @tasks.loop(minutes=30)
    async def cleanup_expired_tickets(self):
        due = await get_tickets_due_for_deletion(datetime.datetime.utcnow())
        for ticket in due:
            channel = self.bot.get_channel(ticket["thread_id"])
            if channel:
                try:
                    await channel.delete()
                except discord.HTTPException:
                    pass
            await mark_ticket_deleted(ticket["thread_id"])

    @cleanup_expired_tickets.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

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
