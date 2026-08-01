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
