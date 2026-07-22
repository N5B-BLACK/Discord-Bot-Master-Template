"""
ملف الإعدادات المركزي.
عند كل عميل جديد: عدّل ملف .env فقط (المتغيرات)، لا تلمس هذا الملف
ولا أي كود آخر إلا لو الميزة نفسها مختلفة جذرياً.
"""

import os

from dotenv import load_dotenv

load_dotenv()  # يقرأ القيم من ملف .env

# ---------------------------------------------------------
# إعدادات أساسية
# ---------------------------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("BOT_PREFIX", "!")

# Guild ID: معرف السيرفر المحدد (لسرعة ظهور Slash Commands ولتقييد الأوامر)
GUILD_ID = int(os.getenv("GUILD_ID", 0)) or None

# ---------------------------------------------------------
# القنوات المسموح فيها استخدام أوامر معينة (اختياري)
# اترك القيمة 0 لو ما في تقييد بقناة محددة
# ---------------------------------------------------------
MOD_LOG_CHANNEL_ID = int(os.getenv("MOD_LOG_CHANNEL_ID", 0)) or None
AI_CHAT_CHANNEL_ID = int(os.getenv("AI_CHAT_CHANNEL_ID", 0)) or None
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", 0)) or None

# ---------------------------------------------------------
# الرولات
# ---------------------------------------------------------
MOD_ROLE_ID = int(os.getenv("MOD_ROLE_ID", 0)) or None
AUTO_ROLE_ID = int(os.getenv("AUTO_ROLE_ID", 0)) or None  # رول تلقائي للأعضاء الجدد

# ---------------------------------------------------------
# مفاتيح خدمات خارجية (AI عبر OpenRouter)
# ---------------------------------------------------------
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
AI_MODEL = os.getenv("AI_MODEL", "anthropic/claude-sonnet-4.5")

# ---------------------------------------------------------
# تخصيص الرسائل (عدّلها حسب هوية كل عميل/سيرفر)
# ---------------------------------------------------------
BOT_NAME = os.getenv("BOT_NAME", "المساعد")
WELCOME_MESSAGE = os.getenv(
    "WELCOME_MESSAGE", "أهلاً فيك {member} بسيرفر {guild}! 🎉"
)
EMBED_COLOR = int(os.getenv("EMBED_COLOR", "0x5865F2"), 16)  # لون ديسكورد الافتراضي
