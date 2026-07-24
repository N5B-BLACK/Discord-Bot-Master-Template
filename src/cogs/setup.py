"""
أمر /setup - يسمح لأدمن السيرفر يظبط كل الإعدادات (رولات وقنوات) بنفسه عن طريق
قوائم اختيار تفاعلية (Select Menus)، بدون الحاجة يدخل أي ID يدويًا أو يلمس أي كود.
كل اختيار بينحفظ فورًا بقاعدة البيانات (MongoDB) خاص بهذا السيرفر بس.

مقسوم صفحتين (ديسكورد بيسمح بحد أقصى 5 صفوف بالمسج الواحد):
- صفحة 1: رول المشرفين، قناة اللوج، قناة الترحيب، الرول التلقائي
- صفحة 2: قناة الذكاء الاصطناعي، رول الدعم (التذاكر)
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils.db import update_guild_setting


def _page1_embed() -> discord.Embed:
    return discord.Embed(
        title="⚙️ إعدادات البوت (1/2)",
        description=(
            "اختر من القوائم تحت لتحديد كل إعداد.\n\n"
            "أي إعداد ما تحدده بيضل بدون تقييد. كل اختيار بينحفظ فورًا - ما تحتاج زر 'حفظ'.\n"
            "اضغط **التالي ▶️** لباقي الإعدادات."
        ),
        color=discord.Color.blurple(),
    )


def _page2_embed() -> discord.Embed:
    return discord.Embed(
        title="⚙️ إعدادات البوت (2/2)",
        description="باقي الإعدادات - نفس المبدأ، كل اختيار بينحفظ فورًا.",
        color=discord.Color.blurple(),
    )


class SetupViewPage1(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="1️⃣ اختر رول المشرفين (Mod Role)",
        row=0,
    )
    async def mod_role_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        role = select.values[0]
        await update_guild_setting(interaction.guild_id, "mod_role_id", role.id)
        await interaction.response.send_message(
            f"✅ تم تحديد رول المشرفين: {role.mention}", ephemeral=True
        )

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="2️⃣ اختر قناة لوج الموديريشن",
        channel_types=[discord.ChannelType.text],
        row=1,
    )
    async def mod_log_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel = select.values[0]
        await update_guild_setting(interaction.guild_id, "mod_log_channel_id", channel.id)
        await interaction.response.send_message(
            f"✅ تم تحديد قناة اللوج: {channel.mention}", ephemeral=True
        )

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="3️⃣ اختر قناة الترحيب",
        channel_types=[discord.ChannelType.text],
        row=2,
    )
    async def welcome_channel_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel = select.values[0]
        await update_guild_setting(interaction.guild_id, "welcome_channel_id", channel.id)
        await interaction.response.send_message(
            f"✅ تم تحديد قناة الترحيب: {channel.mention}", ephemeral=True
        )

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="4️⃣ اختر الرول التلقائي للأعضاء الجدد",
        row=3,
    )
    async def auto_role_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        role = select.values[0]
        await update_guild_setting(interaction.guild_id, "auto_role_id", role.id)
        await interaction.response.send_message(
            f"✅ تم تحديد الرول التلقائي: {role.mention}", ephemeral=True
        )

    @discord.ui.button(label="التالي ▶️", style=discord.ButtonStyle.secondary, row=4)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=_page2_embed(), view=SetupViewPage2())


class SetupViewPage2(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="5️⃣ اختر قناة أمر /ask (الذكاء الاصطناعي)",
        channel_types=[discord.ChannelType.text],
        row=0,
    )
    async def ai_channel_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel = select.values[0]
        await update_guild_setting(interaction.guild_id, "ai_chat_channel_id", channel.id)
        await interaction.response.send_message(
            f"✅ تم تحديد قناة الذكاء الاصطناعي: {channel.mention}", ephemeral=True
        )

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="6️⃣ اختر رول الدعم (مسؤول التذاكر)",
        row=1,
    )
    async def ticket_role_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        role = select.values[0]
        await update_guild_setting(interaction.guild_id, "ticket_support_role_id", role.id)
        await interaction.response.send_message(
            f"✅ تم تحديد رول الدعم: {role.mention}", ephemeral=True
        )

    @discord.ui.button(label="◀️ رجوع", style=discord.ButtonStyle.secondary, row=2)
    async def back_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=_page1_embed(), view=SetupViewPage1())


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setup", description="ظبّط إعدادات البوت لهاد السيرفر (رولات وقنوات)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_command(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=_page1_embed(), view=SetupViewPage1(), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))
