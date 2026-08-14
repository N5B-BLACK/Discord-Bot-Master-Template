"""
Dashboard - Core (Phase 0 refactor, split from the former single dashboard.py).

Shared foundation used by dashboard_pages.py and dashboard_api.py:
- Session handling (Fernet-encrypted cookie, no aiohttp-session - it silently
  failed to set cookies with the aiohttp version pulled in by discord.py)
- The shared auth guard (_guarded_guild) every guild-scoped route goes through
- Setting-group definitions (SETTINGS_GROUPS/LOG_GROUPS) - kept in sync with
  /setup (cogs/setup.py) deliberately; verify both match if a setting changes
- Shared visual identity: dark surfaces, single violet accent, fixed sidebar
  nav (_page_shell/_sidebar_shell) so every page looks identical

This file has no route handlers itself - see dashboard_pages.py (page/HTML
routes) and dashboard_api.py (JSON API routes). dashboard.py wires both into
the aiohttp app.
"""

import base64
import json
import logging

import aiohttp
import discord
from aiohttp import web
from cryptography.fernet import Fernet

import config

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
# Same 34 settings as /setup (cogs/setup.py) - kept in sync deliberately, verify both
# match if a setting is ever added/removed. Split into two pages here for a cleaner
# dashboard: SETTINGS_GROUPS (roles, welcome, AI, tickets, security) on the "Server
# Settings" page, and LOG_GROUPS (every log channel, organized by category) on its own
# dedicated "Logs" page - keeps the 20+ log channels from burying the handful of
# non-log settings a server only sets once and rarely revisits.
# 4th tuple item: whether this field gets an inline color picker (log channels only -
# not roles, and not welcome/ai-chat which already have full control elsewhere:
# welcome via Message Templates, ai-chat isn't an embed at all).
SETTINGS_GROUPS = [
    ("General & Moderation", [
        ("mod_role_id", "Mod Role", "role", False),
        ("welcome_channel_id", "Welcome Channel", "channel", False),
        ("auto_role_id", "Auto-Role (new members)", "role", False),
        ("bot_auto_role_id", "Auto-Role (new bots)", "role", False),
    ]),
    ("AI, Tickets & Security", [
        ("ai_chat_channel_id", "AI Channel (/ask)", "channel", False),
        ("ticket_support_role_id", "Support Role", "role", False),
        ("trap_channel_id", "Trap Channel (auto-ban on post)", "channel", False),
    ]),
]

# Ordered so related log types sit next to each other - this order is also the order
# they render on the Logs page, top to bottom.
LOG_GROUPS = [
    ("Moderation Logs", [
        ("warn_log_channel_id", "Warn Log", "channel", True),
        ("ban_unban_log_channel_id", "Ban / Unban Log", "channel", True),
        ("kicked_log_channel_id", "Kick Log", "channel", True),
        ("timeout_log_channel_id", "Timeout Log", "channel", True),
        ("msg_deleted_log_channel_id", "Message Deletion Log", "channel", True),
        ("message_edit_log_channel_id", "Message Edit Log", "channel", True),
        ("message_bulk_delete_log_channel_id", "Bulk Delete Log", "channel", True),
    ]),
    ("Voice Logs", [
        ("voice_join_leave_log_channel_id", "Join / Leave Log", "channel", True),
        ("voice_switch_log_channel_id", "Switch Log (self)", "channel", True),
        ("voice_move_log_channel_id", "Moved Log (by a mod)", "channel", True),
        ("voice_disconnect_log_channel_id", "Disconnect Log", "channel", True),
        ("voice_mute_log_channel_id", "Mute / Unmute Log", "channel", True),
        ("voice_deafen_log_channel_id", "Deafen / Undeafen Log", "channel", True),
    ]),
    ("Server & Member Logs", [
        ("server_join_leave_log_channel_id", "Server Join / Leave Log", "channel", True),
        ("nickname_change_log_channel_id", "Nickname Change Log", "channel", True),
        ("member_role_change_log_channel_id", "Member Role Change Log", "channel", True),
    ]),
    ("Channel Logs", [
        ("channel_create_log_channel_id", "Channel Created Log", "channel", True),
        ("channel_delete_log_channel_id", "Channel Deleted Log", "channel", True),
        ("channel_update_log_channel_id", "Channel Updated Log", "channel", True),
    ]),
    ("Role Logs", [
        ("role_create_log_channel_id", "Role Created Log", "channel", True),
        ("role_delete_log_channel_id", "Role Deleted Log", "channel", True),
        ("role_update_log_channel_id", "Role Updated Log", "channel", True),
    ]),
    ("Thread Logs", [
        ("thread_create_log_channel_id", "Thread Created Log", "channel", True),
        ("thread_delete_log_channel_id", "Thread Deleted Log", "channel", True),
        ("thread_update_log_channel_id", "Thread Updated Log", "channel", True),
    ]),
    ("Tickets & System", [
        ("ticket_log_channel_id", "Ticket Log Channel", "channel", True),
        ("setup_update_log_channel_id", "Settings-Update Log", "channel", True),
    ]),
]

ALL_SETTINGS_GROUPS = SETTINGS_GROUPS + LOG_GROUPS
VALID_KEYS = {key for _, fields in ALL_SETTINGS_GROUPS for key, _, _, _ in fields}
LOG_COLOR_KEYS = {key for _, fields in ALL_SETTINGS_GROUPS for key, _, _, colorable in fields if colorable}
SETTINGS_LABELS = {key: label for _, fields in ALL_SETTINGS_GROUPS for key, label, _, _ in fields}

NAV_ITEMS = [
    ("", "Overview", "overview"),
    ("settings", "Server Settings", "settings"),
    ("security", "Security", "security"),
    ("leveling", "Leveling", "leveling"),
    ("logs", "Logs", "logs"),
    ("branding", "Branding", "branding"),
    ("tickets", "Ticket Panel", "tickets"),
    ("embeds", "Embed Builder", "embeds"),
    ("templates", "Message Templates", "templates"),
    ("divider", "Auto Divider", "divider"),
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


