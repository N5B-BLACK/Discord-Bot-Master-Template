"""
تكامل الذكاء الاصطناعي: أمر /ask يرسل السؤال لـ OpenRouter (متوافق مع صيغة OpenAI) ويرجع الجواب.
هذا الجزء "قابل للتبديل" بسهولة - بس تغير الموديل بـ AI_MODEL، مش المنطق.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands
from openai import OpenAI

import config
from utils.checks import in_channel

logger = logging.getLogger("bot")

client = (
    OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=config.OPENROUTER_API_KEY,
    )
    if config.OPENROUTER_API_KEY
    else None
)

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
                "⚠️ خدمة الذكاء الاصطناعي غير مفعّلة (لا يوجد مفتاح OpenRouter API).", ephemeral=True
            )
            return

        await interaction.response.defer()
        try:
            response = client.chat.completions.create(
                model=config.AI_MODEL,
                max_tokens=500,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": question},
                ],
            )
            answer = response.choices[0].message.content
            await interaction.followup.send(answer)
        except Exception as e:
            logger.error(f"خطأ باستدعاء AI API عبر OpenRouter: {e}", exc_info=True)
            await interaction.followup.send("⚠️ صار خطأ أثناء التواصل مع خدمة الذكاء الاصطناعي، حاول لاحقاً.")


async def setup(bot: commands.Bot):
    await bot.add_cog(AIChat(bot))
