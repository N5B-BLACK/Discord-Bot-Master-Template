"""
أمر /setup - يسمح لأدمن السيرفر يظبط كل الإعدادات (رولات وقنوات) بنفسه عن طريق
قوائم اختيار تفاعلية (Select Menus)، بدون الحاجة يدخل أي ID يدويًا أو يلمس أي كود.
كل اختيار بينحفظ فورًا بقاعدة البيانات (MongoDB) خاص بهذا السيرفر بس.
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils.db import update_guild_setting


class SetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)  # القوائم بتضل فعالة 5 دقائق

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

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="5️⃣ اختر قناة أمر /ask (الذكاء الاصطناعي)",
        channel_types=[discord.ChannelType.text],
        row=4,
    )
    async def ai_channel_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel = select.values[0]
        await update_guild_setting(interaction.guild_id, "ai_chat_channel_id", channel.id)
        await interaction.response.send_message(
            f"✅ تم تحديد قناة الذكاء الاصطناعي: {channel.mention}", ephemeral=True
        )


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setup", description="ظبّط إعدادات البوت لهاد السيرفر (رولات وقنوات)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="⚙️ إعدادات البوت",
            description=(
                "اختر من القوائم تحت لتحديد كل إعداد.\n\n"
                "أي إعداد ما تحدده بيضل بدون تقييد (الميزة المرتبطة فيه بتشتغل بدون قيد "
                "قناة أو رول). كل اختيار بينحفظ فورًا - ما تحتاج زر 'حفظ'."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, view=SetupView(), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))
