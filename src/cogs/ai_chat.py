"""
تكامل الذكاء الاصطناعي: أمر /ask يرسل السؤال لـ Claude API ويرجع الجواب.
هذا الجزء "قابل للتبديل" بسهولة - بس تغير API key والـ prompt، مش المنطق.
"""

import logging

import discord
from anthropic import Anthropic
from discord import app_commands
from discord.ext import commands

import config
from utils.checks import in_channel

logger = logging.getLogger("bot")

client = Anthropic(api_key=config.ANTHROPIC_API_KEY) if config.ANTHROPIC_API_KEY else None

# عدّل هذا الـ prompt حسب شخصية/هوية البوت لكل عميل
SYSTEM_PROMPT = (
    f"أنت {config.BOT_NAME}، مساعد ذكي داخل سيرفر ديسكورد. "
    "ردودك مختصرة، مفيدة، وودودة. لا تتجاوز 3-4 جمل في الرد ما لم يُطلب خلاف ذلك."
)


class AIChat(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ask", description="اسأل المساعد الذكي أي سؤال")
    @app_commands.describe(question="سؤالك للذكاء الاصطناعي")
    @in_channel(config.AI_CHAT_CHANNEL_ID)
    async def ask(self, interaction: discord.Interaction, question: str):
        if client is None:
            await interaction.response.send_message(
                "⚠️ خدمة الذكاء الاصطناعي غير مفعّلة (لا يوجد مفتاح API).", ephemeral=True
            )
            return

        await interaction.response.defer()
        try:
            response = client.messages.create(
                model=config.AI_MODEL,
                max_tokens=500,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": question}],
            )
            answer = response.content[0].text
            await interaction.followup.send(answer)
        except Exception as e:
            logger.error(f"خطأ باستدعاء AI API: {e}", exc_info=True)
            await interaction.followup.send("⚠️ صار خطأ أثناء التواصل مع خدمة الذكاء الاصطناعي، حاول لاحقاً.")


async def setup(bot: commands.Bot):
    await bot.add_cog(AIChat(bot))
