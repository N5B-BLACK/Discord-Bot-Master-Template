"""
Central config file - bot-wide settings (apply to all servers).
Per-server settings (roles and channels) are stored in the database, configured
via the /setup command (see utils/db.py).
"""

import os

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------
# Core settings
# ---------------------------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("BOT_PREFIX", "!")

# Guild ID: a single server for fast dev/testing (slash commands sync instantly)
# Leave empty once you're done developing so commands sync globally to all servers
GUILD_ID = int(os.getenv("GUILD_ID", 0)) or None

# ---------------------------------------------------------
# Database (stores per-server settings - roles and channels)
# ---------------------------------------------------------
MONGODB_URI = os.getenv("MONGODB_URI")

# ---------------------------------------------------------
# External service keys (AI via OpenRouter)
# ---------------------------------------------------------
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
AI_MODEL = os.getenv("AI_MODEL", "openrouter/free")

# ---------------------------------------------------------
# Message customization (adjust per client/server identity)
# ---------------------------------------------------------
BOT_NAME = os.getenv("BOT_NAME", "Assistant")
WELCOME_MESSAGE = os.getenv(
    "WELCOME_MESSAGE", "Welcome {member} to {guild}! 🎉"
)
EMBED_COLOR = int(os.getenv("EMBED_COLOR", "0x5865F2"), 16)

# ---------------------------------------------------------
# Web dashboard (Discord OAuth2 login)
# ---------------------------------------------------------
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DASHBOARD_SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY")
REDIRECT_URI = os.getenv("REDIRECT_URI")
# Derived from REDIRECT_URI (e.g. https://your-app.onrender.com/callback -> https://your-app.onrender.com)
# used to build "open in dashboard" links from within Discord (e.g. /embed builder).
DASHBOARD_BASE_URL = REDIRECT_URI.rsplit("/callback", 1)[0] if REDIRECT_URI else None

# ---------------------------------------------------------
# Billing (Paddle subscriptions - Phase 5)
# ---------------------------------------------------------
PADDLE_API_KEY = os.getenv("PADDLE_API_KEY")
PADDLE_WEBHOOK_SECRET = os.getenv("PADDLE_WEBHOOK_SECRET")
PADDLE_PRICE_ID_PRO = os.getenv("PADDLE_PRICE_ID_PRO")  # the Paddle Price ID (pri_...) for the Pro monthly subscription
PADDLE_CLIENT_TOKEN = os.getenv("PADDLE_CLIENT_TOKEN")  # client-side token (starts test_/live_), used by Paddle.js in the browser
PADDLE_ENVIRONMENT = os.getenv("PADDLE_ENVIRONMENT", "production")  # "production" or "sandbox"
# Display-only - what the upgrade page shows. Change this freely; it does NOT
# affect what Paddle actually charges (that's controlled by PADDLE_PRICE_ID_PRO
# in your Paddle Dashboard) - keep the two in sync manually if you change price.
PRO_PRICE_DISPLAY = os.getenv("PRO_PRICE_DISPLAY", "$9.99/mo")

# Optional: base64-encoded Netscape-format cookies.txt exported from a real, logged-in
# YouTube session - improves music playback reliability on cloud hosts (see
# utils/music_source.py's module docstring). Entirely optional; everything works
# without it, just less reliably when YouTube's bot-check triggers.
YTDLP_COOKIES_B64 = os.getenv("YTDLP_COOKIES_B64")
