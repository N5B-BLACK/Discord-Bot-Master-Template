"""
Web dashboard - Discord OAuth2 login, a list of servers the logged-in user can manage,
and a settings editor per server (mirrors /setup exactly, saved to the same database).

Layout: a persistent left sidebar (Overview / Server Settings / Branding / Ticket Panel /
Embed Builder) once a server is selected, matching the standard SaaS-admin-panel pattern
(fixed sidebar nav + card-based content area, single accent color, dark surfaces) rather
than one long scrolling page. Every guild-scoped page shares the same sidebar shell
(_sidebar_shell) so navigation is identical everywhere.

Session handling is manual, via a single Fernet-encrypted cookie - no aiohttp-session
dependency (it silently failed to set cookies with the aiohttp version pulled in by
discord.py). The cookie only stores the access token + basic user info; the guild list
is always re-fetched from Discord fresh (a full guild list is too large for a cookie).

Runs on the same aiohttp app as the health check server (see main.py) - no separate
hosting needed.
"""

import base64
import json
import logging
from urllib.parse import urlencode

import aiohttp
import discord
from aiohttp import web
from cryptography.fernet import Fernet

import config
from utils.db import (
    delete_embed_draft,
    get_embed_draft,
    get_guild_settings,
    list_embed_drafts,
    save_embed_draft,
    set_log_color,
    update_guild_setting,
)
from utils.embed_builder import LIMITS, EmbedValidationError, blank_embed_json, to_discord_embed
from utils.message_templates import TEMPLATE_SLOT_KEYS, TEMPLATE_SLOTS

logger = logging.getLogger("bot")

DISCORD_API = "https://discord.com/api"
ADMINISTRATOR = 0x8
MANAGE_GUILD = 0x20
COOKIE_NAME = "session_data"
MAX_AGE = 60 * 60 * 24 * 7  # 7 days

# Same 19 settings as /setup (cogs/setup.py) - kept in sync deliberately, verify both
# match if a setting is ever added/removed. Grouped differently than /setup's pages
# here, though: /setup is forced into 4-items-per-page by Discord's 5-action-row
# limit, but the dashboard has no such constraint, so these groups are organized by
# broader topic instead of Discord's UI mechanics.
# 4th tuple item: whether this field gets an inline color picker (only true log-channel
# types - not roles, and not welcome/ai-chat which already have full control elsewhere:
# welcome via Message Templates, ai-chat isn't an embed at all).
SETTINGS_GROUPS = [
    ("General & Moderation", [
        ("mod_role_id", "Mod Role", "role", False),
        ("warn_log_channel_id", "Warn Log Channel", "channel", True),
        ("welcome_channel_id", "Welcome Channel", "channel", False),
        ("auto_role_id", "Auto-Role (new members)", "role", False),
        ("bot_auto_role_id", "Auto-Role (new bots)", "role", False),
    ]),
    ("AI & Tickets", [
        ("ai_chat_channel_id", "AI Channel (/ask)", "channel", False),
        ("ticket_support_role_id", "Support Role", "role", False),
        ("ticket_log_channel_id", "Ticket Log Channel", "channel", True),
    ]),
    ("Voice Logs", [
        ("voice_join_leave_log_channel_id", "Join / Leave Log", "channel", True),
        ("voice_switch_log_channel_id", "Channel Switch Log", "channel", True),
        ("voice_disconnect_log_channel_id", "Disconnect Log", "channel", True),
        ("voice_mute_log_channel_id", "Mute / Unmute Log", "channel", True),
        ("voice_deafen_log_channel_id", "Deafen / Undeafen Log", "channel", True),
    ]),
    ("More Logs", [
        ("ban_unban_log_channel_id", "Ban / Unban Log", "channel", True),
        ("server_join_leave_log_channel_id", "Server Join / Leave Log", "channel", True),
        ("msg_deleted_log_channel_id", "Message Deletion Log", "channel", True),
        ("timeout_log_channel_id", "Timeout Log", "channel", True),
        ("kicked_log_channel_id", "Kick Log", "channel", True),
        ("setup_update_log_channel_id", "Settings Update Log", "channel", True),
    ]),
]
VALID_KEYS = {key for _, fields in SETTINGS_GROUPS for key, _, _, _ in fields}
LOG_COLOR_KEYS = {key for _, fields in SETTINGS_GROUPS for key, _, _, colorable in fields if colorable}

NAV_ITEMS = [
    ("", "Overview", "overview"),
    ("settings", "Server Settings", "settings"),
    ("branding", "Branding", "branding"),
    ("tickets", "Ticket Panel", "tickets"),
    ("embeds", "Embed Builder", "embeds"),
    ("templates", "Message Templates", "templates"),
]


# ---------------------------------------------------------
# Session helpers (Fernet-encrypted cookie)
# ---------------------------------------------------------
def _fernet() -> Fernet:
    raw = config.DASHBOARD_SECRET_KEY.encode("utf-8")
    padded = raw.ljust(32, b"0")[:32]
    return Fernet(base64.urlsafe_b64encode(padded))


def _read_session(request: web.Request) -> dict:
    raw_cookie = request.cookies.get(COOKIE_NAME)
    if not raw_cookie:
        return {}
    try:
        decrypted = _fernet().decrypt(raw_cookie.encode("utf-8"))
        return json.loads(decrypted)
    except Exception:
        return {}


def _write_session(response: web.Response, data: dict) -> None:
    encrypted = _fernet().encrypt(json.dumps(data).encode("utf-8"))
    response.set_cookie(
        COOKIE_NAME,
        encrypted.decode("utf-8"),
        httponly=True,
        secure=True,
        samesite="Lax",
        max_age=MAX_AGE,
    )


def _can_manage(guild: dict) -> bool:
    perms = int(guild.get("permissions", 0))
    return bool(perms & ADMINISTRATOR) or bool(perms & MANAGE_GUILD)


async def _require_session(request: web.Request):
    """Returns (access_token, user) or None - shared guard for every guild-scoped page."""
    session_data = _read_session(request)
    access_token = session_data.get("access_token")
    if not access_token:
        return None
    return access_token, session_data.get("user")


async def _verify_guild_access(access_token: str, guild_id: int) -> bool:
    """Re-checks with Discord that this user can manage this specific server -
    never trust a guild_id from the URL alone."""
    async with aiohttp.ClientSession() as http:
        resp = await http.get(
            f"{DISCORD_API}/users/@me/guilds",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status != 200:
            return False
        guilds = await resp.json()
    return any(str(g["id"]) == str(guild_id) and _can_manage(g) for g in guilds)


async def _guarded_guild(request: web.Request, bot, guild_id: int, json_errors: bool = False):
    """Full guard used by every guild-scoped route: session -> guild exists -> user can
    manage it. Returns (access_token, guild) on success, or a ready-to-return
    web.Response on failure."""
    session = await _require_session(request)
    if session is None:
        return None, (
            web.json_response({"error": "not authenticated"}, status=401)
            if json_errors
            else web.HTTPFound("/login")
        )
    access_token, _ = session

    guild = bot.get_guild(guild_id)
    if guild is None:
        msg = "bot not in this server" if json_errors else "Bot is not in this server."
        return None, (
            web.json_response({"error": msg}, status=404) if json_errors else web.Response(text=msg, status=404)
        )

    if not await _verify_guild_access(access_token, guild_id):
        msg = "forbidden" if json_errors else "You don't have permission to manage this server."
        return None, (
            web.json_response({"error": msg}, status=403) if json_errors else web.Response(text=msg, status=403)
        )

    return access_token, guild


# ---------------------------------------------------------
# Shared visual identity - dark surfaces, single violet accent, fixed sidebar nav.
# Chosen deliberately over a generic Discord-blurple clone: the audience (server
# admins) already lives inside Discord's own UI daily, so the dashboard reads as
# its own distinct tool rather than a re-skinned Discord settings page.
# ---------------------------------------------------------
BASE_STYLES = """
:root {
    --bg: #0b0a0f;
    --sidebar-bg: #121017;
    --surface: #17151f;
    --surface-hover: #1d1a27;
    --border: #2a2733;
    --accent: #7c5cff;
    --accent-glow: rgba(124, 92, 255, 0.35);
    --accent-soft: #a78bfa;
    --text: #f2f1f7;
    --text-muted: #9c98ab;
    --text-faint: #66627a;
    --success: #34d399;
    --danger: #f87171;
    --radius: 12px;
}
* { box-sizing: border-box; }
html, body { overflow-x: hidden; }
body {
    margin: 0;
    background: var(--bg);
    background-image: radial-gradient(circle at 100% 0%, rgba(124, 92, 255, 0.06), transparent 45%);
    color: var(--text);
    font-family: 'Inter', sans-serif;
    min-height: 100vh;
}
a { color: inherit; }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 10px; }
.topbar {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 18px 32px;
    border-bottom: 1px solid var(--border);
    position: sticky;
    top: 0;
    background: rgba(11, 10, 15, 0.85);
    backdrop-filter: blur(8px);
    z-index: 10;
}
@media (max-width: 480px) {
    .topbar { padding: 14px 16px; }
}
.brand {
    font-family: 'Space Grotesk', sans-serif;
    font-weight: 600;
    font-size: 16px;
    letter-spacing: -0.01em;
    display: flex;
    align-items: center;
    gap: 10px;
}
.brand-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--accent);
    box-shadow: 0 0 12px var(--accent-glow);
    flex-shrink: 0;
}
.topbar .spacer { margin-left: auto; }
.link-btn {
    color: var(--text-muted);
    text-decoration: none;
    font-size: 13px;
    font-weight: 500;
    padding: 8px 14px;
    border: 1px solid var(--border);
    border-radius: 8px;
    transition: border-color 0.15s, color 0.15s;
    background: none;
    cursor: pointer;
    font-family: 'Inter', sans-serif;
}
.link-btn:hover { border-color: var(--accent); color: var(--text); }
.eyebrow {
    color: var(--accent-soft);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.07em;
    text-transform: uppercase;
    margin-bottom: 8px;
}
h1 {
    font-family: 'Space Grotesk', sans-serif;
    font-size: 26px;
    font-weight: 700;
    letter-spacing: -0.02em;
    margin: 0 0 6px;
}
.subtitle { color: var(--text-muted); font-size: 14px; margin: 0 0 32px; }
"""

# Login/servers-list page (no guild selected yet -> no sidebar, just a topbar)
GUILD_LIST_STYLES = """
.container { max-width: 720px; margin: 0 auto; padding: 48px 24px 80px; }
.guild-list { display: flex; flex-direction: column; gap: 10px; }
.guild-card {
    display: flex; align-items: center; gap: 16px;
    padding: 14px 18px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    text-decoration: none;
    color: var(--text);
    transition: border-color 0.15s, box-shadow 0.15s, transform 0.15s, background 0.15s;
}
.guild-card:hover {
    background: var(--surface-hover);
    border-color: var(--accent);
    box-shadow: 0 0 0 1px var(--accent), 0 8px 24px -8px var(--accent-glow);
    transform: translateY(-1px);
}
.guild-icon { width: 44px; height: 44px; border-radius: 50%; flex-shrink: 0; border: 1px solid var(--border); }
.guild-name { font-weight: 500; font-size: 15px; flex: 1; }
.manage-label { color: var(--accent-soft); font-size: 13px; font-weight: 600; }
.empty-state {
    padding: 40px 24px; text-align: center;
    background: var(--surface); border: 1px dashed var(--border); border-radius: var(--radius);
    color: var(--text-muted); font-size: 14px; line-height: 1.6;
}
@media (max-width: 480px) {
    .container { padding: 28px 16px 60px; }
    .manage-label { display: none; }
}
"""

# Sidebar shell used by every guild-scoped page
SIDEBAR_STYLES = """
.app-layout { display: flex; min-height: 100vh; }
.sidebar {
    width: 240px; flex-shrink: 0;
    background: var(--sidebar-bg);
    border-right: 1px solid var(--border);
    display: flex; flex-direction: column;
    position: sticky; top: 0; height: 100vh;
}
.sidebar-header { padding: 20px 18px; border-bottom: 1px solid var(--border); }
.sidebar-guild { display: flex; align-items: center; gap: 10px; margin-top: 14px; }
.sidebar-guild img { width: 36px; height: 36px; border-radius: 50%; border: 1px solid var(--border); }
.sidebar-guild-name {
    font-size: 14px; font-weight: 600; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; max-width: 160px;
}
.sidebar-nav { flex: 1; padding: 14px 10px; display: flex; flex-direction: column; gap: 2px; }
.sidebar-link {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 12px; border-radius: 8px;
    color: var(--text-muted); text-decoration: none;
    font-size: 13.5px; font-weight: 500;
    transition: background 0.15s, color 0.15s;
}
.sidebar-link:hover { background: var(--surface-hover); color: var(--text); }
.sidebar-link.active {
    background: linear-gradient(90deg, var(--accent-glow), transparent);
    color: var(--text); font-weight: 600;
    box-shadow: inset 2px 0 0 var(--accent);
}
.sidebar-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--text-faint); flex-shrink: 0; }
.sidebar-link.active .sidebar-dot { background: var(--accent); box-shadow: 0 0 8px var(--accent-glow); }
.sidebar-footer { padding: 14px 10px; border-top: 1px solid var(--border); display: flex; flex-direction: column; gap: 2px; }
.main-area { flex: 1; min-width: 0; }
.page-container { max-width: 860px; padding: 40px 36px 100px; }
.page-container.wide { max-width: 1180px; }

/* Mobile top bar - hidden on desktop, shown only under the breakpoint below */
.mobile-topbar {
    display: none;
    align-items: center; gap: 12px;
    padding: 14px 16px; border-bottom: 1px solid var(--border);
    position: sticky; top: 0; background: rgba(11, 10, 15, 0.92); backdrop-filter: blur(8px); z-index: 15;
}
.mobile-topbar-guild { display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 600; overflow: hidden; }
.mobile-topbar-guild img { width: 26px; height: 26px; border-radius: 50%; flex-shrink: 0; }
.mobile-topbar-guild span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hamburger-btn {
    display: none; flex-direction: column; justify-content: center; gap: 4px;
    width: 34px; height: 34px; background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; cursor: pointer; flex-shrink: 0; padding: 0;
}
.hamburger-btn span { width: 16px; height: 2px; background: var(--text); margin: 0 auto; border-radius: 2px; }
.sidebar-backdrop {
    display: none; position: fixed; inset: 0; background: rgba(0, 0, 0, 0.55); z-index: 19;
}
.sidebar-backdrop.show { display: block; }

@media (max-width: 860px) {
    .sidebar {
        position: fixed; top: 0; left: 0; z-index: 20; height: 100vh;
        transform: translateX(-100%);
        transition: transform 0.2s ease-out;
        box-shadow: 24px 0 48px rgba(0, 0, 0, 0.45);
    }
    .sidebar.open { transform: translateX(0); }
    .mobile-topbar, .hamburger-btn { display: flex; }
    .page-container { padding: 24px 16px 60px; max-width: 100%; }
    .page-container.wide { max-width: 100%; }
    .stat-grid, .quick-grid { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 480px) {
    .stat-grid, .quick-grid { grid-template-columns: 1fr; }
}
"""

SETTINGS_STYLES = """
.group {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 22px 22px 6px;
    margin-bottom: 18px;
}
.group h2 {
    font-family: 'Space Grotesk', sans-serif;
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--accent-soft);
    margin: 0 0 14px;
}
.group-hint { color: var(--text-muted); font-size: 13px; margin: -8px 0 14px; }
.field {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 0; border-top: 1px solid var(--border); gap: 16px;
}
.group h2 + .field, .group h2 + .group-hint + .field { border-top: none; }
.field label { font-size: 14px; color: var(--text); }
.field-right { display: flex; align-items: center; gap: 10px; }

/* Professional dropdown treatment, applied to every <select> site-wide - native
   arrow removed, replaced with a custom chevron, consistent with input styling. */
select {
    appearance: none;
    -webkit-appearance: none;
    background-image: url("data:image/svg+xml;charset=UTF-8,%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3e%3cpath fill='%239c98ab' d='M4 6l4 4 4-4' stroke='%239c98ab' stroke-width='1.4' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3e%3c/svg%3e");
    background-repeat: no-repeat;
    background-position: right 12px center;
    padding-right: 34px !important;
}
.field select, .field input[type="text"], .field input[type="url"] {
    background-color: var(--bg); color: var(--text);
    border: 1px solid var(--border); border-radius: 8px;
    padding: 8px 12px; min-width: 220px; font-size: 13px;
    font-family: 'Inter', sans-serif; cursor: pointer;
    transition: border-color 0.15s, box-shadow 0.15s;
}
.field input[type="text"], .field input[type="url"] { cursor: text; }
.field select:hover { border-color: var(--text-faint); }
.field select:focus, .field input:focus {
    outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-glow);
}
.field input[type="color"] {
    width: 44px; height: 36px; padding: 2px;
    background: var(--bg); border: 1px solid var(--border); border-radius: 8px; cursor: pointer;
}
.log-color-picker { position: relative; display: inline-flex; align-items: center; }
.log-color-picker input[type="color"] {
    width: 30px; height: 30px; padding: 2px;
    background: var(--bg); border: 1px solid var(--border); border-radius: 7px; cursor: pointer;
    opacity: .45; transition: opacity 0.15s, border-color 0.15s;
}
.log-color-picker.is-custom input[type="color"] { opacity: 1; border-color: var(--accent-soft); }
.log-color-picker input[type="color"]:hover { opacity: 1; }
.log-color-reset {
    position: absolute; top: -6px; right: -6px;
    width: 15px; height: 15px; border-radius: 50%;
    background: var(--danger); color: #1a0f0f; border: none;
    font-size: 10px; line-height: 15px; text-align: center; cursor: pointer; padding: 0;
}
.status { font-size: 12px; font-weight: 600; opacity: 0; transition: opacity 0.2s; white-space: nowrap; }
.status.show { opacity: 1; }
.status.ok { color: var(--success); }
.status.err { color: var(--danger); }
.action-row {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 0; gap: 16px;
}
.btn {
    background: var(--accent); color: #fff; border: none; border-radius: 8px;
    padding: 9px 16px; font-size: 13px; font-weight: 600; cursor: pointer;
    font-family: 'Inter', sans-serif; transition: filter 0.15s;
}
.btn:hover { filter: brightness(1.1); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.secondary { background: var(--surface); border: 1px solid var(--border); color: var(--text); }
.btn.danger { background: var(--danger); color: #1a0f0f; }

@media (max-width: 640px) {
    .field { flex-direction: column; align-items: stretch; gap: 8px; padding: 14px 0; }
    .field label { font-weight: 600; }
    .field-right { width: 100%; flex-wrap: wrap; }
    .field select, .field input[type="text"], .field input[type="url"] { min-width: 0; width: 100%; }
    .action-row { flex-direction: column; align-items: stretch; gap: 10px; }
    .action-row .field-right { justify-content: space-between; }
    .group { padding: 18px 16px 4px; }
}
"""

OVERVIEW_STYLES = """
.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 14px; margin-bottom: 28px; }
.stat-card {
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 18px 20px;
}
.stat-card .stat-value { font-family: 'Space Grotesk', sans-serif; font-size: 26px; font-weight: 700; }
.stat-card .stat-label { color: var(--text-muted); font-size: 12.5px; margin-top: 4px; }
.quick-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; }
.quick-card {
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 18px 20px; text-decoration: none; color: var(--text);
    transition: border-color 0.15s, transform 0.15s;
}
.quick-card:hover { border-color: var(--accent); transform: translateY(-1px); }
.quick-card .quick-title { font-weight: 600; font-size: 14.5px; margin-bottom: 4px; }
.quick-card .quick-desc { color: var(--text-muted); font-size: 12.5px; line-height: 1.5; }
"""

# ---------------------------------------------------------
# Embed Builder page - two-column layout: form (left) + a live Discord-style
# preview (right) that mirrors the real embed pixel-for-pixel as closely as
# CSS reasonably allows, so what you see is what gets sent.
# ---------------------------------------------------------
EMBED_BUILDER_STYLES = """
.embed-layout { display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 24px; align-items: start; }
@media (max-width: 980px) { .embed-layout { grid-template-columns: 1fr; } }
.ebrow { display: flex; gap: 10px; margin-bottom: 8px; }
.ebrow .field-col { flex: 1; display: flex; flex-direction: column; gap: 6px; }
.ebrow label { font-size: 12px; color: var(--text-muted); font-weight: 600; }
.eb-input, .eb-textarea {
    background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 8px;
    padding: 9px 12px; font-size: 13px; font-family: 'Inter', sans-serif; width: 100%;
}
.eb-textarea { resize: vertical; min-height: 70px; }
.eb-input:focus, .eb-textarea:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-glow); }
.eb-charcount { font-size: 11px; color: var(--text-faint); text-align: right; margin-top: 2px; }
.eb-charcount.over { color: var(--danger); }
.eb-section { margin-bottom: 6px; }
.eb-section-title {
    font-family: 'Space Grotesk', sans-serif; font-size: 12px; text-transform: uppercase;
    letter-spacing: 0.06em; color: var(--accent-soft); margin: 22px 0 10px;
}
.eb-color-row { display: flex; align-items: center; gap: 10px; }
.eb-color-row input[type="color"] { width: 40px; height: 36px; padding: 2px; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; cursor: pointer; }
.eb-field-card {
    background: var(--bg); border: 1px solid var(--border); border-radius: 10px;
    padding: 12px; margin-bottom: 10px;
}
.eb-field-card .ebrow { margin-bottom: 6px; }
.eb-field-controls { display: flex; align-items: center; justify-content: space-between; }
.eb-inline-toggle { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-muted); }
.eb-remove-field { background: none; border: none; color: var(--danger); font-size: 12px; cursor: pointer; font-family: 'Inter', sans-serif; }
.eb-toolbar { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-top: 22px; }
.eb-toolbar select { background: var(--surface); color: var(--text); border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; font-size: 13px; }

/* --- Live preview, styled to resemble Discord's real embed rendering --- */
.preview-shell {
    position: sticky; top: 84px;
    background: #313338; border-radius: var(--radius); padding: 16px;
    font-family: 'gg sans', 'Inter', sans-serif;
}
.preview-label { color: var(--text-faint); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 10px; }
.dc-embed {
    background: #2b2d31; border-left: 4px solid #5865f2; border-radius: 4px;
    padding: 10px 14px 14px; display: grid; grid-template-columns: 1fr auto; gap: 10px; color: #dbdee1;
}
.dc-embed-main { min-width: 0; }
.dc-author { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 600; color: #f2f3f5; margin-bottom: 6px; }
.dc-author img { width: 20px; height: 20px; border-radius: 50%; }
.dc-title { font-size: 15px; font-weight: 600; color: #00a8fc; margin-bottom: 6px; line-height: 1.3; word-break: break-word; }
.dc-title.no-link { color: #f2f3f5; }
.dc-desc { font-size: 13.5px; line-height: 1.45; white-space: pre-wrap; word-break: break-word; margin-bottom: 8px; }
.dc-fields { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px 12px; margin-bottom: 8px; }
.dc-field { min-width: 0; }
.dc-field.block { grid-column: 1 / -1; }
.dc-field-name { font-size: 13px; font-weight: 600; color: #f2f3f5; margin-bottom: 2px; word-break: break-word; }
.dc-field-value { font-size: 13px; line-height: 1.4; white-space: pre-wrap; word-break: break-word; }
.dc-thumb { width: 80px; height: 80px; border-radius: 4px; object-fit: cover; align-self: start; }
.dc-image { grid-column: 1 / -1; max-width: 100%; border-radius: 4px; margin-top: 6px; }
.dc-footer { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #dbdee1; margin-top: 8px; }
.dc-footer img { width: 16px; height: 16px; border-radius: 50%; }
.dc-empty { color: #949ba4; font-size: 13px; padding: 20px 0; text-align: center; }

@media (max-width: 640px) {
    .ebrow { flex-direction: column; gap: 12px; }
    .eb-toolbar { flex-direction: column; align-items: stretch; }
    .eb-toolbar select, .eb-toolbar input, .eb-toolbar button { width: 100%; }
    .eb-toolbar span[style] { display: none; } /* the flex spacer isn't needed when stacked */
    .preview-shell { position: static; margin-top: 20px; }
    .dc-fields { grid-template-columns: repeat(2, 1fr); }
}
"""


def _page_shell(title: str, extra_styles: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        {BASE_STYLES}
        {extra_styles}
    </style>
</head>
<body>
    {body}
</body>
</html>"""


def _sidebar_shell(guild: discord.Guild, icon_url: str, active: str, content: str, wide: bool = False) -> str:
    """Wraps page content in the standard sidebar layout used by every guild-scoped page."""
    nav_html = ""
    for path, label, key in NAV_ITEMS:
        href = f"/dashboard/{guild.id}/{path}" if path else f"/dashboard/{guild.id}"
        cls = "sidebar-link active" if key == active else "sidebar-link"
        nav_html += f'<a class="{cls}" href="{href}"><span class="sidebar-dot"></span>{label}</a>\n'

    container_cls = "page-container wide" if wide else "page-container"
    return f"""
    <div class="app-layout">
        <div class="sidebar-backdrop" id="sidebar-backdrop" onclick="document.getElementById('sidebar').classList.remove('open'); this.classList.remove('show');"></div>
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <div class="brand"><span class="brand-dot"></span> Bot Dashboard</div>
                <div class="sidebar-guild">
                    <img src="{icon_url}" alt="">
                    <span class="sidebar-guild-name">{guild.name}</span>
                </div>
            </div>
            <div class="sidebar-nav">{nav_html}</div>
            <div class="sidebar-footer">
                <a class="sidebar-link" href="/dashboard"><span class="sidebar-dot"></span>All servers</a>
                <a class="sidebar-link" href="/logout"><span class="sidebar-dot"></span>Logout</a>
            </div>
        </div>
        <div class="main-area">
            <div class="mobile-topbar">
                <button class="hamburger-btn" aria-label="Open menu" onclick="document.getElementById('sidebar').classList.add('open'); document.getElementById('sidebar-backdrop').classList.add('show');">
                    <span></span><span></span><span></span>
                </button>
                <div class="mobile-topbar-guild">
                    <img src="{icon_url}" alt="">
                    <span>{guild.name}</span>
                </div>
            </div>
            <div class="{container_cls}">
                {content}
            </div>
        </div>
    </div>
    """


# ---------------------------------------------------------
# OAuth routes
# ---------------------------------------------------------
async def login(request: web.Request) -> web.Response:
    params = {
        "client_id": config.DISCORD_CLIENT_ID,
        "redirect_uri": config.REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds",
    }
    return web.HTTPFound(f"{DISCORD_API}/oauth2/authorize?{urlencode(params)}")


async def callback(request: web.Request) -> web.Response:
    code = request.query.get("code")
    if not code:
        return web.Response(text="Missing authorization code.", status=400)

    async with aiohttp.ClientSession() as http:
        token_resp = await http.post(
            f"{DISCORD_API}/oauth2/token",
            data={
                "client_id": config.DISCORD_CLIENT_ID,
                "client_secret": config.DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": config.REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_resp.status != 200:
            logger.error(f"OAuth token exchange failed: {await token_resp.text()}")
            return web.Response(text="Login failed during token exchange.", status=400)
        token_data = await token_resp.json()
        access_token = token_data["access_token"]

        headers = {"Authorization": f"Bearer {access_token}"}
        user_resp = await http.get(f"{DISCORD_API}/users/@me", headers=headers)
        user = await user_resp.json()

    session_data = {
        "access_token": access_token,
        "user": {"id": user["id"], "username": user["username"], "avatar": user.get("avatar")},
    }

    response = web.HTTPFound("/dashboard")
    _write_session(response, session_data)
    return response


async def logout(request: web.Request) -> web.Response:
    response = web.HTTPFound("/")
    response.del_cookie(COOKIE_NAME)
    return response


async def dashboard_home(request: web.Request, bot) -> web.Response:
    session_data = _read_session(request)
    user = session_data.get("user")
    access_token = session_data.get("access_token")
    if not user or not access_token:
        return web.HTTPFound("/login")

    async with aiohttp.ClientSession() as http:
        guilds_resp = await http.get(
            f"{DISCORD_API}/users/@me/guilds",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if guilds_resp.status != 200:
            response = web.HTTPFound("/login")
            response.del_cookie(COOKIE_NAME)
            return response
        user_guilds = await guilds_resp.json()

    bot_guild_ids = {g.id for g in bot.guilds}
    manageable = [g for g in user_guilds if _can_manage(g) and int(g["id"]) in bot_guild_ids]

    cards = ""
    for g in manageable:
        icon_url = (
            f"https://cdn.discordapp.com/icons/{g['id']}/{g['icon']}.png"
            if g.get("icon")
            else "https://cdn.discordapp.com/embed/avatars/0.png"
        )
        cards += f"""
        <a class="guild-card" href="/dashboard/{g['id']}">
            <img class="guild-icon" src="{icon_url}" alt="">
            <span class="guild-name">{g['name']}</span>
            <span class="manage-label">Manage →</span>
        </a>
        """

    empty_state = """
    <div class="empty-state">
        No servers to manage yet.<br>
        The bot needs to be in the server, and you need Manage Server permission there.
    </div>
    """

    body = f"""
    <div class="topbar">
        <div class="brand"><span class="brand-dot"></span> Bot Dashboard</div>
        <div class="spacer"></div>
        <a class="link-btn" href="/logout">Logout</a>
    </div>
    <div class="container">
        <div class="eyebrow">Signed in as {user['username']}</div>
        <h1>Your servers</h1>
        <p class="subtitle">Pick a server to configure roles, channels, branding, and embeds.</p>
        <div class="guild-list">{cards or empty_state}</div>
    </div>
    """
    return web.Response(text=_page_shell("Bot Dashboard", GUILD_LIST_STYLES, body), content_type="text/html")


def _build_options(guild: discord.Guild, kind: str, current_value):
    """Builds <option> tags for a role or text-channel select, filtering out
    @everyone / managed (bot, booster, integration) roles, matching /setup's behavior."""
    options = ['<option value="">— Not set —</option>']
    if kind == "role":
        items = [r for r in guild.roles if not r.managed and r.name != "@everyone"]
        items.sort(key=lambda r: r.position, reverse=True)
    else:
        items = list(guild.text_channels)
        items.sort(key=lambda c: c.position)

    for item in items:
        selected = " selected" if current_value and item.id == current_value else ""
        label = f"@{item.name}" if kind == "role" else f"#{item.name}"
        options.append(f'<option value="{item.id}"{selected}>{label}</option>')
    return "\n".join(options)


def _icon_url(guild: discord.Guild) -> str:
    return guild.icon.url if guild.icon else "https://cdn.discordapp.com/embed/avatars/0.png"


# ---------------------------------------------------------
# Guild-scoped pages
# ---------------------------------------------------------
async def overview_page(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id)
    if not isinstance(guard, discord.Guild):
        return guard
    guild = guard

    settings = await get_guild_settings(guild_id)
    configured_count = sum(1 for k in VALID_KEYS if settings.get(k))
    drafts = await list_embed_drafts(guild_id)

    content = f"""
    <div class="eyebrow">Server</div>
    <h1>{guild.name}</h1>
    <p class="subtitle">Everything here saves instantly and stays in sync with /setup in Discord.</p>

    <div class="stat-grid">
        <div class="stat-card"><div class="stat-value">{guild.member_count or '—'}</div><div class="stat-label">Members</div></div>
        <div class="stat-card"><div class="stat-value">{configured_count}/{len(VALID_KEYS)}</div><div class="stat-label">Settings configured</div></div>
        <div class="stat-card"><div class="stat-value">{len(drafts)}</div><div class="stat-label">Saved embed drafts</div></div>
    </div>

    <div class="eb-section-title" style="margin-top:0;">Quick links</div>
    <div class="quick-grid">
        <a class="quick-card" href="/dashboard/{guild.id}/settings">
            <div class="quick-title">Server Settings →</div>
            <div class="quick-desc">Mod role, log channels, welcome, tickets, voice &amp; audit logs.</div>
        </a>
        <a class="quick-card" href="/dashboard/{guild.id}/branding">
            <div class="quick-title">Branding →</div>
            <div class="quick-desc">The default color, icon, and footer applied to the bot's own embeds.</div>
        </a>
        <a class="quick-card" href="/dashboard/{guild.id}/embeds">
            <div class="quick-title">Embed Builder →</div>
            <div class="quick-desc">Design fully custom embeds with a live preview, then send or save them.</div>
        </a>
        <a class="quick-card" href="/dashboard/{guild.id}/tickets">
            <div class="quick-title">Ticket Panel →</div>
            <div class="quick-desc">Post the "Open Ticket" button in any channel.</div>
        </a>
    </div>
    """
    body = _sidebar_shell(guild, _icon_url(guild), "overview", content)
    return web.Response(text=_page_shell(f"{guild.name} · Overview", SIDEBAR_STYLES + OVERVIEW_STYLES, body), content_type="text/html")


async def guild_settings_page(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id)
    if not isinstance(guard, discord.Guild):
        return guard
    guild = guard

    settings = await get_guild_settings(guild_id)
    log_colors = settings.get("log_colors") or {}

    groups_html = ""
    for group_name, fields in SETTINGS_GROUPS:
        rows = ""
        for key, label, kind, colorable in fields:
            current = settings.get(key)
            options = _build_options(guild, kind, current)
            color_picker = ""
            if colorable:
                current_color = log_colors.get(key)
                color_hex = f"#{current_color:06x}" if current_color is not None else "#7c5cff"
                is_custom = current_color is not None
                reset_btn = (
                    f'<button type="button" class="log-color-reset" data-color-for="{key}" title="Reset to default color">×</button>'
                    if is_custom else ""
                )
                color_picker = (
                    f'<span class="log-color-picker{" is-custom" if is_custom else ""}" data-color-wrap-for="{key}">'
                    f'<input type="color" data-color-for="{key}" value="{color_hex}">'
                    f'{reset_btn}'
                    f'</span>'
                )
            rows += f"""
            <div class="field">
                <label>{label}</label>
                <div class="field-right">
                    {color_picker}
                    <select data-key="{key}">{options}</select>
                    <span class="status"></span>
                </div>
            </div>
            """
        groups_html += f'<div class="group"><h2>{group_name}</h2>{rows}</div>'

    content = f"""
    <div class="eyebrow">Server settings</div>
    <h1>Roles &amp; log channels</h1>
    <p class="subtitle">Changes save instantly — identical to running /setup in Discord.</p>
    {groups_html}
    <script>const API_BASE = '/dashboard/{guild.id}/api';</script>
    <script>{SETTINGS_JS}</script>
    """
    body = _sidebar_shell(guild, _icon_url(guild), "settings", content)
    return web.Response(text=_page_shell(f"{guild.name} · Settings", SIDEBAR_STYLES + SETTINGS_STYLES, body), content_type="text/html")


async def branding_page(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id)
    if not isinstance(guard, discord.Guild):
        return guard
    guild = guard

    settings = await get_guild_settings(guild_id)
    color_value = settings.get("embed_color")
    color_hex = f"#{color_value:06x}" if color_value else "#7c5cff"
    icon_value = settings.get("embed_icon_url") or ""
    footer_text_value = settings.get("embed_footer_text") or ""
    footer_icon_value = settings.get("embed_footer_icon_url") or ""

    content = f"""
    <div class="eyebrow">Branding</div>
    <h1>Default embed identity</h1>
    <p class="subtitle">
        Applied automatically to every embed the bot generates itself (tickets, welcome, warnings, logs).
        Discord doesn't allow custom fonts in embeds - color, images, and footer are what's customizable.
    </p>
    <div class="group">
        <div class="field">
            <label>Embed color</label>
            <div class="field-right"><input type="color" id="brand-color" value="{color_hex}"></div>
        </div>
        <div class="field">
            <label>Logo / icon URL</label>
            <div class="field-right"><input type="url" id="brand-icon" placeholder="https://..." value="{icon_value}"></div>
        </div>
        <div class="field">
            <label>Footer text</label>
            <div class="field-right"><input type="text" id="brand-footer-text" placeholder="Powered by..." value="{footer_text_value}"></div>
        </div>
        <div class="field">
            <label>Footer icon URL</label>
            <div class="field-right"><input type="url" id="brand-footer-icon" placeholder="https://..." value="{footer_icon_value}"></div>
        </div>
        <div class="action-row">
            <span></span>
            <div class="field-right">
                <button class="btn" id="branding-save">Save branding</button>
                <span class="status" id="branding-status"></span>
            </div>
        </div>
    </div>
    <script>const API_BASE = '/dashboard/{guild.id}/api';</script>
    <script>{BRANDING_JS}</script>
    """
    body = _sidebar_shell(guild, _icon_url(guild), "branding", content)
    return web.Response(text=_page_shell(f"{guild.name} · Branding", SIDEBAR_STYLES + SETTINGS_STYLES, body), content_type="text/html")


async def tickets_page(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id)
    if not isinstance(guard, discord.Guild):
        return guard
    guild = guard

    channel_options = _build_options(guild, "channel", None)
    content = f"""
    <div class="eyebrow">Tickets</div>
    <h1>Ticket panel</h1>
    <p class="subtitle">Posts the "🎫 Open Ticket" button panel in the channel you pick. Requires a Support Role set in Server Settings.</p>
    <div class="group">
        <div class="action-row">
            <select id="panel-channel-select">{channel_options}</select>
            <div class="field-right">
                <button class="btn" id="post-panel-btn">Post panel</button>
                <span class="status" id="panel-status"></span>
            </div>
        </div>
    </div>
    <script>const API_BASE = '/dashboard/{guild.id}/api';</script>
    <script>{TICKETS_JS}</script>
    """
    body = _sidebar_shell(guild, _icon_url(guild), "tickets", content)
    return web.Response(text=_page_shell(f"{guild.name} · Ticket Panel", SIDEBAR_STYLES + SETTINGS_STYLES, body), content_type="text/html")


async def templates_page(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id)
    if not isinstance(guard, discord.Guild):
        return guard
    guild = guard

    settings = await get_guild_settings(guild_id)
    drafts = await list_embed_drafts(guild_id)
    draft_names = [d["name"] for d in drafts]

    rows = ""
    for slot_key, label, placeholders in TEMPLATE_SLOTS:
        current = settings.get(slot_key) or ""
        options = ['<option value="">— Default (built-in) —</option>']
        for n in draft_names:
            selected = " selected" if n == current else ""
            options.append(f'<option value="{n}"{selected}>{n}</option>')
        placeholder_chips = " ".join(f"<code>{p}</code>" for p in placeholders)
        rows += f"""
        <div class="group">
            <h2>{label}</h2>
            <p class="group-hint">Available placeholders: {placeholder_chips}</p>
            <div class="field">
                <label>Embed draft to use</label>
                <div class="field-right">
                    <select data-slot="{slot_key}">{"".join(options)}</select>
                    <span class="status"></span>
                </div>
            </div>
        </div>
        """

    no_drafts_hint = (
        '<p class="group-hint" style="margin-bottom:20px;">You have no saved embed drafts yet - '
        f'build one in <a href="/dashboard/{guild.id}/embeds" style="color:var(--accent-soft);">Embed Builder</a> first, '
        "then come back here to assign it to a slot.</p>"
        if not draft_names
        else ""
    )

    content = f"""
    <div class="eyebrow">Message Templates</div>
    <h1>Customize built-in messages</h1>
    <p class="subtitle">
        Point any of these at a saved Embed Builder draft to fully replace the bot's default
        wording and design for that message. Leave on "Default" to keep the built-in one.
        Placeholders are substituted automatically wherever they appear in the draft.
    </p>
    {no_drafts_hint}
    {rows}
    <script>const API_BASE = '/dashboard/{guild.id}/api';</script>
    <script>{TEMPLATES_JS}</script>
    """
    body = _sidebar_shell(guild, _icon_url(guild), "templates", content)
    return web.Response(
        text=_page_shell(f"{guild.name} · Message Templates", SIDEBAR_STYLES + SETTINGS_STYLES, body),
        content_type="text/html",
    )



async def embeds_page(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id)
    if not isinstance(guard, discord.Guild):
        return guard
    guild = guard

    channel_options = _build_options(guild, "channel", None)
    content = f"""
    <div class="eyebrow">Embed Builder</div>
    <h1>Design a custom embed</h1>
    <p class="subtitle">Full control - title, description, color, author, thumbnail, image, footer, up to {LIMITS['max_fields']} fields, and a timestamp. The preview on the right is exactly what gets sent.</p>

    <div class="eb-toolbar">
        <select id="draft-select"><option value="">— New draft —</option></select>
        <button class="btn secondary" id="load-draft-btn">Load</button>
        <button class="btn secondary" id="delete-draft-btn">Delete</button>
        <span style="flex:1"></span>
        <input class="eb-input" id="draft-name" placeholder="Draft name (e.g. rules)" style="width:180px;">
        <button class="btn" id="save-draft-btn">Save draft</button>
    </div>

    <div class="embed-layout" style="margin-top:20px;">
        <div>
            <div class="group" style="padding-top:18px;">
                <div class="eb-section-title" style="margin-top:0;">Content</div>
                <div class="ebrow">
                    <div class="field-col">
                        <label>Title</label>
                        <input class="eb-input" id="eb-title" maxlength="{LIMITS['title']}">
                        <div class="eb-charcount" data-for="eb-title" data-limit="{LIMITS['title']}"></div>
                    </div>
                </div>
                <div class="ebrow">
                    <div class="field-col">
                        <label>Title URL (optional, makes the title clickable)</label>
                        <input class="eb-input" id="eb-url" placeholder="https://...">
                    </div>
                </div>
                <div class="ebrow">
                    <div class="field-col">
                        <label>Description</label>
                        <textarea class="eb-textarea" id="eb-description" maxlength="{LIMITS['description']}"></textarea>
                        <div class="eb-charcount" data-for="eb-description" data-limit="{LIMITS['description']}"></div>
                    </div>
                </div>
                <div class="ebrow">
                    <div class="field-col">
                        <label>Color</label>
                        <div class="eb-color-row">
                            <input type="color" id="eb-color" value="#5865f2">
                            <span id="eb-color-hex" style="font-size:12px;color:var(--text-muted);">#5865F2</span>
                        </div>
                    </div>
                    <div class="field-col">
                        <label><input type="checkbox" id="eb-timestamp"> Show current timestamp</label>
                    </div>
                </div>

                <div class="eb-section-title">Author</div>
                <div class="ebrow">
                    <div class="field-col"><label>Name</label><input class="eb-input" id="eb-author-name" maxlength="{LIMITS['author_name']}"></div>
                </div>
                <div class="ebrow">
                    <div class="field-col"><label>Icon URL</label><input class="eb-input" id="eb-author-icon" placeholder="https://..."></div>
                    <div class="field-col"><label>Link URL</label><input class="eb-input" id="eb-author-url" placeholder="https://..."></div>
                </div>

                <div class="eb-section-title">Images</div>
                <div class="ebrow">
                    <div class="field-col"><label>Thumbnail URL (small, top-right)</label><input class="eb-input" id="eb-thumbnail" placeholder="https://..."></div>
                </div>
                <div class="ebrow">
                    <div class="field-col"><label>Image URL (large, bottom)</label><input class="eb-input" id="eb-image" placeholder="https://..."></div>
                </div>

                <div class="eb-section-title">Footer</div>
                <div class="ebrow">
                    <div class="field-col"><label>Text</label><input class="eb-input" id="eb-footer-text" maxlength="{LIMITS['footer_text']}"></div>
                </div>
                <div class="ebrow">
                    <div class="field-col"><label>Icon URL</label><input class="eb-input" id="eb-footer-icon" placeholder="https://..."></div>
                </div>
            </div>

            <div class="group" style="padding-top:18px;">
                <div class="eb-section-title" style="margin-top:0;">Fields <span style="color:var(--text-faint); text-transform:none; letter-spacing:0;">(max {LIMITS['max_fields']})</span></div>
                <div id="eb-fields-list"></div>
                <div class="action-row" style="padding-top:4px;">
                    <button class="btn secondary" id="add-field-btn">+ Add field</button>
                    <span></span>
                </div>
            </div>

            <div class="eb-toolbar">
                <select id="send-channel-select">{channel_options}</select>
                <button class="btn" id="send-embed-btn">Send to channel</button>
                <span class="status" id="send-status"></span>
            </div>
        </div>

        <div class="preview-shell">
            <div class="preview-label">Live preview</div>
            <div class="dc-embed" id="dc-preview"></div>
        </div>
    </div>

    <script>const API_BASE = '/dashboard/{guild.id}/api'; const FIELD_LIMITS = {{name: {LIMITS['field_name']}, value: {LIMITS['field_value']}, max_fields: {LIMITS['max_fields']}}};</script>
    <script>{EMBED_BUILDER_JS}</script>
    """
    body = _sidebar_shell(guild, _icon_url(guild), "embeds", content, wide=True)
    return web.Response(
        text=_page_shell(f"{guild.name} · Embed Builder", SIDEBAR_STYLES + SETTINGS_STYLES + EMBED_BUILDER_STYLES, body),
        content_type="text/html",
    )


# ---------------------------------------------------------
# JS - one small shared save-pattern per page, plus the larger embed builder script
# ---------------------------------------------------------
SETTINGS_JS = """
document.querySelectorAll('select[data-key]').forEach(function (sel) {
    sel.addEventListener('change', async function () {
        const key = sel.dataset.key;
        const value = sel.value || null;
        const statusEl = sel.closest('.field-right').querySelector('.status');
        try {
            const res = await fetch(API_BASE + '/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key: key, value: value }),
            });
            if (!res.ok) throw new Error('save failed');
            statusEl.textContent = 'Saved';
            statusEl.className = 'status show ok';
        } catch (e) {
            statusEl.textContent = 'Error';
            statusEl.className = 'status show err';
        }
        setTimeout(function () { statusEl.classList.remove('show'); }, 1500);
    });
});

async function saveLogColor(key, hexOrNull) {
    const wrap = document.querySelector('[data-color-wrap-for="' + key + '"]');
    const statusEl = wrap ? wrap.closest('.field-right').querySelector('.status') : null;
    try {
        const res = await fetch(API_BASE + '/log-color', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: key, color: hexOrNull }),
        });
        if (!res.ok) throw new Error('save failed');
        if (statusEl) { statusEl.textContent = 'Saved'; statusEl.className = 'status show ok'; }
        if (wrap) wrap.classList.toggle('is-custom', !!hexOrNull);
        let resetBtn = wrap ? wrap.querySelector('.log-color-reset') : null;
        if (hexOrNull && wrap && !resetBtn) {
            resetBtn = document.createElement('button');
            resetBtn.type = 'button';
            resetBtn.className = 'log-color-reset';
            resetBtn.dataset.colorFor = key;
            resetBtn.title = 'Reset to default color';
            resetBtn.textContent = '\\u00d7';
            wrap.appendChild(resetBtn);
            resetBtn.addEventListener('click', onResetClick);
        } else if (!hexOrNull && resetBtn) {
            resetBtn.remove();
        }
    } catch (e) {
        if (statusEl) { statusEl.textContent = 'Error'; statusEl.className = 'status show err'; }
    }
    if (statusEl) setTimeout(function () { statusEl.classList.remove('show'); }, 1500);
}

document.querySelectorAll('input[type="color"][data-color-for]').forEach(function (input) {
    input.addEventListener('input', function () { saveLogColor(input.dataset.colorFor, input.value); });
});

function onResetClick(e) {
    const key = e.target.dataset.colorFor;
    const wrap = document.querySelector('[data-color-wrap-for="' + key + '"]');
    const input = wrap ? wrap.querySelector('input[type="color"]') : null;
    if (input) input.value = '#7c5cff';
    saveLogColor(key, null);
}
document.querySelectorAll('.log-color-reset').forEach(function (btn) { btn.addEventListener('click', onResetClick); });
"""

TEMPLATES_JS = """
document.querySelectorAll('select[data-slot]').forEach(function (sel) {
    sel.addEventListener('change', async function () {
        const slot = sel.dataset.slot;
        const value = sel.value || null;
        const statusEl = sel.closest('.field-right').querySelector('.status');
        try {
            const res = await fetch(API_BASE + '/templates', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ slot: slot, value: value }),
            });
            if (!res.ok) throw new Error('save failed');
            statusEl.textContent = 'Saved';
            statusEl.className = 'status show ok';
        } catch (e) {
            statusEl.textContent = 'Error';
            statusEl.className = 'status show err';
        }
        setTimeout(function () { statusEl.classList.remove('show'); }, 1500);
    });
});
"""

BRANDING_JS = """
const brandingSaveBtn = document.getElementById('branding-save');
brandingSaveBtn.addEventListener('click', async function () {
    const statusEl = document.getElementById('branding-status');
    const payload = {
        embed_color: document.getElementById('brand-color').value,
        embed_icon_url: document.getElementById('brand-icon').value.trim() || null,
        embed_footer_text: document.getElementById('brand-footer-text').value.trim() || null,
        embed_footer_icon_url: document.getElementById('brand-footer-icon').value.trim() || null,
    };
    brandingSaveBtn.disabled = true;
    try {
        const res = await fetch(API_BASE + '/branding', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error('save failed');
        statusEl.textContent = 'Saved';
        statusEl.className = 'status show ok';
    } catch (e) {
        statusEl.textContent = 'Error';
        statusEl.className = 'status show err';
    }
    brandingSaveBtn.disabled = false;
    setTimeout(function () { statusEl.classList.remove('show'); }, 1500);
});
"""

TICKETS_JS = """
const panelPostBtn = document.getElementById('post-panel-btn');
panelPostBtn.addEventListener('click', async function () {
    const channelId = document.getElementById('panel-channel-select').value;
    const statusEl = document.getElementById('panel-status');
    if (!channelId) {
        statusEl.textContent = 'Pick a channel first';
        statusEl.className = 'status show err';
        setTimeout(function () { statusEl.classList.remove('show'); }, 2000);
        return;
    }
    panelPostBtn.disabled = true;
    try {
        const res = await fetch(API_BASE + '/post-ticket-panel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channel_id: channelId }),
        });
        if (!res.ok) throw new Error('post failed');
        statusEl.textContent = 'Panel posted';
        statusEl.className = 'status show ok';
    } catch (e) {
        statusEl.textContent = 'Failed to post';
        statusEl.className = 'status show err';
    }
    panelPostBtn.disabled = false;
    setTimeout(function () { statusEl.classList.remove('show'); }, 2000);
});
"""

EMBED_BUILDER_JS = """
let fields = []; // {name, value, inline}

function esc(s) {
    return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function currentData() {
    return {
        title: document.getElementById('eb-title').value,
        description: document.getElementById('eb-description').value,
        url: document.getElementById('eb-url').value.trim(),
        color: parseInt(document.getElementById('eb-color').value.replace('#',''), 16),
        author_name: document.getElementById('eb-author-name').value,
        author_icon_url: document.getElementById('eb-author-icon').value.trim(),
        author_url: document.getElementById('eb-author-url').value.trim(),
        thumbnail_url: document.getElementById('eb-thumbnail').value.trim(),
        image_url: document.getElementById('eb-image').value.trim(),
        footer_text: document.getElementById('eb-footer-text').value,
        footer_icon_url: document.getElementById('eb-footer-icon').value.trim(),
        timestamp: document.getElementById('eb-timestamp').checked,
        fields: fields,
    };
}

function loadData(data) {
    document.getElementById('eb-title').value = data.title || '';
    document.getElementById('eb-description').value = data.description || '';
    document.getElementById('eb-url').value = data.url || '';
    const colorHex = '#' + (data.color != null ? data.color : 0x5865F2).toString(16).padStart(6, '0');
    document.getElementById('eb-color').value = colorHex;
    document.getElementById('eb-author-name').value = data.author_name || '';
    document.getElementById('eb-author-icon').value = data.author_icon_url || '';
    document.getElementById('eb-author-url').value = data.author_url || '';
    document.getElementById('eb-thumbnail').value = data.thumbnail_url || '';
    document.getElementById('eb-image').value = data.image_url || '';
    document.getElementById('eb-footer-text').value = data.footer_text || '';
    document.getElementById('eb-footer-icon').value = data.footer_icon_url || '';
    document.getElementById('eb-timestamp').checked = !!data.timestamp;
    fields = (data.fields || []).slice(0, FIELD_LIMITS.max_fields);
    renderFields();
    renderPreview();
}

function renderFields() {
    const list = document.getElementById('eb-fields-list');
    list.innerHTML = '';
    fields.forEach(function (f, i) {
        const card = document.createElement('div');
        card.className = 'eb-field-card';
        card.innerHTML = `
            <div class="ebrow">
                <div class="field-col"><label>Field ${i + 1} name</label><input class="eb-input fname" maxlength="${FIELD_LIMITS.name}" value="${esc(f.name)}"></div>
            </div>
            <div class="ebrow">
                <div class="field-col"><label>Value</label><textarea class="eb-textarea fvalue" maxlength="${FIELD_LIMITS.value}" style="min-height:50px;">${esc(f.value)}</textarea></div>
            </div>
            <div class="eb-field-controls">
                <label class="eb-inline-toggle"><input type="checkbox" class="finline" ${f.inline ? 'checked' : ''}> Inline</label>
                <button class="eb-remove-field">Remove</button>
            </div>
        `;
        card.querySelector('.fname').addEventListener('input', function (e) { fields[i].name = e.target.value; renderPreview(); });
        card.querySelector('.fvalue').addEventListener('input', function (e) { fields[i].value = e.target.value; renderPreview(); });
        card.querySelector('.finline').addEventListener('change', function (e) { fields[i].inline = e.target.checked; renderPreview(); });
        card.querySelector('.eb-remove-field').addEventListener('click', function () { fields.splice(i, 1); renderFields(); renderPreview(); });
        list.appendChild(card);
    });
}

function renderPreview() {
    const d = currentData();
    const el = document.getElementById('dc-preview');
    el.style.borderLeftColor = document.getElementById('eb-color').value;
    document.getElementById('eb-color-hex').textContent = document.getElementById('eb-color').value.toUpperCase();

    const hasContent = d.title || d.description || d.fields.length || d.author_name || d.image_url;
    if (!hasContent) {
        el.innerHTML = '<div class="dc-empty">Start typing to see the preview</div>';
        return;
    }

    let fieldsHtml = '';
    if (d.fields.length) {
        fieldsHtml = '<div class="dc-fields">' + d.fields.map(function (f) {
            const cls = f.inline ? 'dc-field' : 'dc-field block';
            return `<div class="${cls}"><div class="dc-field-name">${esc(f.name) || '\\u200b'}</div><div class="dc-field-value">${esc(f.value)}</div></div>`;
        }).join('') + '</div>';
    }

    el.innerHTML = `
        <div class="dc-embed-main">
            ${d.author_name ? `<div class="dc-author">${d.author_icon_url ? `<img src="${esc(d.author_icon_url)}">` : ''}${esc(d.author_name)}</div>` : ''}
            ${d.title ? `<div class="dc-title ${d.url ? '' : 'no-link'}">${esc(d.title)}</div>` : ''}
            ${d.description ? `<div class="dc-desc">${esc(d.description)}</div>` : ''}
            ${fieldsHtml}
            ${d.image_url ? `<img class="dc-image" src="${esc(d.image_url)}">` : ''}
            ${(d.footer_text || d.timestamp) ? `<div class="dc-footer">${d.footer_icon_url ? `<img src="${esc(d.footer_icon_url)}">` : ''}${esc(d.footer_text)}${d.footer_text && d.timestamp ? ' • ' : ''}${d.timestamp ? 'Today' : ''}</div>` : ''}
        </div>
        ${d.thumbnail_url ? `<img class="dc-thumb" src="${esc(d.thumbnail_url)}">` : '<div></div>'}
    `;
}

document.querySelectorAll('#eb-title, #eb-description, #eb-url, #eb-author-name, #eb-author-icon, #eb-author-url, #eb-thumbnail, #eb-image, #eb-footer-text, #eb-footer-icon, #eb-timestamp, #eb-color')
    .forEach(function (input) { input.addEventListener('input', renderPreview); input.addEventListener('change', renderPreview); });

document.querySelectorAll('.eb-charcount').forEach(function (counter) {
    const target = document.getElementById(counter.dataset.for);
    const limit = parseInt(counter.dataset.limit, 10);
    function update() {
        const len = target.value.length;
        counter.textContent = len + ' / ' + limit;
        counter.classList.toggle('over', len > limit);
    }
    target.addEventListener('input', update);
    update();
});

document.getElementById('add-field-btn').addEventListener('click', function () {
    if (fields.length >= FIELD_LIMITS.max_fields) return;
    fields.push({ name: '', value: '', inline: true });
    renderFields();
    renderPreview();
});

async function refreshDraftList(selectName) {
    const sel = document.getElementById('draft-select');
    const res = await fetch(API_BASE + '/embeds/list');
    const data = await res.json();
    sel.innerHTML = '<option value="">— New draft —</option>' +
        data.drafts.map(function (n) { return `<option value="${esc(n)}">${esc(n)}</option>`; }).join('');
    if (selectName) sel.value = selectName;
}

document.getElementById('load-draft-btn').addEventListener('click', async function () {
    const name = document.getElementById('draft-select').value;
    if (!name) { loadData({}); document.getElementById('draft-name').value = ''; return; }
    const res = await fetch(API_BASE + '/embeds/' + encodeURIComponent(name));
    if (!res.ok) return;
    const data = await res.json();
    loadData(data.embed_json);
    document.getElementById('draft-name').value = name;
});

document.getElementById('delete-draft-btn').addEventListener('click', async function () {
    const name = document.getElementById('draft-select').value;
    if (!name) return;
    if (!confirm('Delete "' + name + '"?')) return;
    await fetch(API_BASE + '/embeds/' + encodeURIComponent(name), { method: 'DELETE' });
    await refreshDraftList();
    loadData({});
    document.getElementById('draft-name').value = '';
});

document.getElementById('save-draft-btn').addEventListener('click', async function () {
    const name = document.getElementById('draft-name').value.trim();
    if (!name) { alert('Give this draft a name first.'); return; }
    const res = await fetch(API_BASE + '/embeds/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name, embed_json: currentData() }),
    });
    const data = await res.json();
    if (!res.ok) { alert(data.error || 'Failed to save.'); return; }
    await refreshDraftList(name);
});

document.getElementById('send-embed-btn').addEventListener('click', async function () {
    const channelId = document.getElementById('send-channel-select').value;
    const statusEl = document.getElementById('send-status');
    if (!channelId) { statusEl.textContent = 'Pick a channel'; statusEl.className = 'status show err'; return; }
    const res = await fetch(API_BASE + '/embeds/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel_id: channelId, embed_json: currentData() }),
    });
    const data = await res.json();
    if (!res.ok) { statusEl.textContent = data.error || 'Failed'; statusEl.className = 'status show err'; }
    else { statusEl.textContent = 'Sent!'; statusEl.className = 'status show ok'; }
    setTimeout(function () { statusEl.classList.remove('show'); }, 2500);
});

refreshDraftList();
renderFields();
renderPreview();
"""


# ---------------------------------------------------------
# API routes (JSON) - all guild-scoped, all re-verify Discord permissions server-side
# ---------------------------------------------------------
async def save_guild_setting(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    key = body.get("key")
    value = body.get("value")
    if key not in VALID_KEYS:
        return web.json_response({"error": "invalid setting key"}, status=400)

    parsed_value = int(value) if value else None
    await update_guild_setting(guild_id, key, parsed_value)
    return web.json_response({"ok": True})


async def save_branding(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    color_hex = body.get("embed_color")
    if color_hex:
        try:
            color_int = int(color_hex.lstrip("#"), 16)
        except ValueError:
            return web.json_response({"error": "invalid color"}, status=400)
        await update_guild_setting(guild_id, "embed_color", color_int)

    for key in ("embed_icon_url", "embed_footer_text", "embed_footer_icon_url"):
        await update_guild_setting(guild_id, key, body.get(key) or None)

    return web.json_response({"ok": True})


async def save_log_color(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    key = body.get("key")
    if key not in LOG_COLOR_KEYS:
        return web.json_response({"error": "invalid log type"}, status=400)

    color_hex = body.get("color")
    color_int = None
    if color_hex:
        try:
            color_int = int(color_hex.lstrip("#"), 16)
        except ValueError:
            return web.json_response({"error": "invalid color"}, status=400)

    await set_log_color(guild_id, key, color_int)
    return web.json_response({"ok": True})


async def save_template_slot(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    slot = body.get("slot")
    if slot not in TEMPLATE_SLOT_KEYS:
        return web.json_response({"error": "invalid template slot"}, status=400)

    value = (body.get("value") or "").strip() or None
    if value:
        draft = await get_embed_draft(guild_id, value)
        if not draft:
            return web.json_response({"error": "that draft no longer exists"}, status=400)

    await update_guild_setting(guild_id, slot, value)
    return web.json_response({"ok": True})


async def post_ticket_panel(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard
    guild = guard

    body = await request.json()
    try:
        channel_id = int(body.get("channel_id"))
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid channel"}, status=400)

    channel = guild.get_channel(channel_id)
    if channel is None:
        return web.json_response({"error": "channel not found"}, status=404)

    # imported here (not at module level) to avoid a circular import between
    # dashboard.py and cogs.tickets
    from cogs.tickets import TicketPanelView, build_panel_embed

    embed = await build_panel_embed(guild_id, guild.name)
    try:
        await channel.send(embed=embed, view=TicketPanelView())
    except discord.Forbidden:
        return web.json_response({"error": "bot lacks permission to post in that channel"}, status=403)

    return web.json_response({"ok": True})


async def embeds_list(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    drafts = await list_embed_drafts(guild_id)
    return web.json_response({"drafts": [d["name"] for d in drafts]})


async def embeds_get_one(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    name = request.match_info["name"]
    draft = await get_embed_draft(guild_id, name)
    if not draft:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"name": draft["name"], "embed_json": draft["embed_json"]})


async def embeds_delete_one(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    name = request.match_info["name"]
    await delete_embed_draft(guild_id, name)
    return web.json_response({"ok": True})


async def embeds_save(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard

    body = await request.json()
    name = (body.get("name") or "").strip()[:80]
    embed_json = body.get("embed_json") or blank_embed_json()
    if not name:
        return web.json_response({"error": "draft needs a name"}, status=400)

    try:
        to_discord_embed(embed_json)  # validates without sending
    except EmbedValidationError as e:
        return web.json_response({"error": str(e)}, status=400)

    session_data = _read_session(request)
    user_id = int(session_data.get("user", {}).get("id", 0))
    await save_embed_draft(guild_id, name, embed_json, user_id)
    return web.json_response({"ok": True})


async def embeds_send(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id, json_errors=True)
    if not isinstance(guard, discord.Guild):
        return guard
    guild = guard

    body = await request.json()
    embed_json = body.get("embed_json") or {}
    try:
        channel_id = int(body.get("channel_id"))
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid channel"}, status=400)

    channel = guild.get_channel(channel_id)
    if channel is None:
        return web.json_response({"error": "channel not found"}, status=404)

    try:
        embed = to_discord_embed(embed_json)
    except EmbedValidationError as e:
        return web.json_response({"error": str(e)}, status=400)

    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        return web.json_response({"error": "bot lacks permission to post in that channel"}, status=403)

    return web.json_response({"ok": True})


def setup_dashboard_routes(app: web.Application, bot):
    app.router.add_get("/login", login)
    app.router.add_get("/callback", callback)
    app.router.add_get("/logout", logout)
    app.router.add_get("/dashboard", lambda request: dashboard_home(request, bot))

    app.router.add_get("/dashboard/{guild_id}", lambda request: overview_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/settings", lambda request: guild_settings_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/branding", lambda request: branding_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/tickets", lambda request: tickets_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/embeds", lambda request: embeds_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/templates", lambda request: templates_page(request, bot))

    app.router.add_post("/dashboard/{guild_id}/api/settings", lambda request: save_guild_setting(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/log-color", lambda request: save_log_color(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/branding", lambda request: save_branding(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/templates", lambda request: save_template_slot(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/post-ticket-panel", lambda request: post_ticket_panel(request, bot))
    app.router.add_get("/dashboard/{guild_id}/api/embeds/list", lambda request: embeds_list(request, bot))
    app.router.add_get("/dashboard/{guild_id}/api/embeds/{name}", lambda request: embeds_get_one(request, bot))
    app.router.add_delete("/dashboard/{guild_id}/api/embeds/{name}", lambda request: embeds_delete_one(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/embeds/save", lambda request: embeds_save(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/embeds/send", lambda request: embeds_send(request, bot))
