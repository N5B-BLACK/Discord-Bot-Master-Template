"""
AI integration: /ask sends a question to OpenRouter (OpenAI-compatible) and returns the answer.
The allowed channel is configured per-server via /setup (not a static .env value).
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands
from openai import OpenAI

import config
from utils.checks import in_configured_channel

logger = logging.getLogger("bot")

client = (
    OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=config.OPENROUTER_API_KEY,
    )
    if config.OPENROUTER_API_KEY
    else None
)

SYSTEM_PROMPT = (
    f"You are {config.BOT_NAME}, a helpful assistant inside a Discord server. "
    "Keep replies short, useful, and friendly. Don't exceed 3-4 sentences unless asked otherwise."
)


class AIChat(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ask", description="Ask the AI assistant anything")
    @app_commands.describe(question="Your question for the AI")
    @in_configured_channel("ai_chat_channel_id")
    async def ask(self, interaction: discord.Interaction, question: str):
        if client is None:
            await interaction.response.send_message(
                "⚠️ The AI service isn't enabled (no OpenRouter API key set).", ephemeral=True
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
            logger.info(f"AI responded using model: {response.model}")
            await interaction.followup.send(answer)
        except Exception as e:
            logger.error(f"Error calling the AI API via OpenRouter: {e}", exc_info=True)
            await interaction.followup.send("⚠️ Something went wrong contacting the AI service, try again later.")


async def setup(bot: commands.Bot):
    await bot.add_cog(AIChat(bot))
