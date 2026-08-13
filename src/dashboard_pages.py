"""
Dashboard - Page routes (Phase 0 refactor, split from the former single dashboard.py).

Every function here renders full HTML (OAuth login/callback/logout, the guild
list, and every guild-scoped settings page). JSON API routes (the "Save"
buttons call these) live in dashboard_api.py instead - see there for those.

Import note: SETTINGS_JS / TEMPLATES_JS / DIVIDER_JS / BRANDING_JS /
TICKETS_JS / EMBED_BUILDER_JS are defined further down in this same file and
referenced inside page functions above them - safe in Python since function
bodies aren't evaluated until called (by which point the whole module,
including these constants, has finished loading).
"""

import aiohttp
import discord
from aiohttp import web
from urllib.parse import urlencode

import config
from dashboard_core import (
    COOKIE_NAME,
    DISCORD_API,
    EMBED_BUILDER_STYLES,
    GUILD_LIST_STYLES,
    LOG_GROUPS,
    OVERVIEW_STYLES,
    SETTINGS_GROUPS,
    SETTINGS_STYLES,
    SIDEBAR_STYLES,
    VALID_KEYS,
    _can_manage,
    _guarded_guild,
    _page_shell,
    _read_session,
    _sidebar_shell,
    _write_session,
    logger,
)
from utils.db import get_guild_settings, list_embed_drafts
from utils.embed_builder import LIMITS
from utils.message_templates import TEMPLATE_SLOTS

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
    <h1>Roles &amp; channels</h1>
    <p class="subtitle">Changes save instantly — identical to running /setup in Discord. Looking for log channels? See the <a href="/dashboard/{guild.id}/logs" style="color:var(--accent-soft);">Logs</a> page.</p>
    {groups_html}
    <script>const API_BASE = '/dashboard/{guild.id}/api';</script>
    <script>{SETTINGS_JS}</script>
    """
    body = _sidebar_shell(guild, _icon_url(guild), "settings", content)
    return web.Response(text=_page_shell(f"{guild.name} · Settings", SIDEBAR_STYLES + SETTINGS_STYLES, body), content_type="text/html")


async def logs_page(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id)
    if not isinstance(guard, discord.Guild):
        return guard
    guild = guard

    settings = await get_guild_settings(guild_id)
    log_colors = settings.get("log_colors") or {}

    def _anchor_id(name: str) -> str:
        return "log-" + name.lower().replace(" ", "-").replace("&", "and").replace("/", "")

    quick_nav = " · ".join(
        f'<a href="#{_anchor_id(name)}" style="color:var(--accent-soft);">{name}</a>' for name, _ in LOG_GROUPS
    )

    groups_html = ""
    for group_name, fields in LOG_GROUPS:
        rows = ""
        for key, label, kind, colorable in fields:
            current = settings.get(key)
            options = _build_options(guild, kind, current)
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
        groups_html += f'<div class="group" id="{_anchor_id(group_name)}"><h2>{group_name}</h2>{rows}</div>'

    content = f"""
    <div class="eyebrow">Logs</div>
    <h1>Every log channel, in one place</h1>
    <p class="subtitle">Jump to a category: {quick_nav}</p>
    {groups_html}
    <script>const API_BASE = '/dashboard/{guild.id}/api';</script>
    <script>{SETTINGS_JS}</script>
    """
    body = _sidebar_shell(guild, _icon_url(guild), "logs", content)
    return web.Response(text=_page_shell(f"{guild.name} · Logs", SIDEBAR_STYLES + SETTINGS_STYLES, body), content_type="text/html")


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



async def divider_page(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id)
    if not isinstance(guard, discord.Guild):
        return guard
    guild = guard

    settings = await get_guild_settings(guild_id)
    divider = settings.get("auto_divider") or {}
    enabled = bool(divider.get("enabled"))
    image_url = divider.get("image_url") or ""
    channel_ids = divider.get("channel_ids") or []

    channel_rows = ""
    for cid in channel_ids:
        channel = guild.get_channel(cid)
        label = f"#{channel.name}" if channel else f"Unknown channel ({cid})"
        channel_rows += f"""
        <div class="field">
            <label>{label}</label>
            <div class="field-right">
                <button class="btn danger" data-remove-channel="{cid}">Remove</button>
            </div>
        </div>
        """
    if not channel_rows:
        channel_rows = '<p class="group-hint">No channels added yet - pick one below.</p>'

    add_channel_options = _build_options(guild, "channel", None)

    preview_html = (
        f'<img src="{image_url}" alt="Divider preview" style="max-width:100%;border-radius:8px;border:1px solid var(--border);margin-top:10px;">'
        if image_url
        else '<p class="group-hint">No image set yet.</p>'
    )

    content = f"""
    <div class="eyebrow">Auto Divider</div>
    <h1>Post an image after every message</h1>
    <p class="subtitle">
        In the channels you choose below, the bot automatically posts this image right after
        every message a member sends - useful as a visual separator in showcase or media channels.
    </p>

    <div class="group">
        <div class="action-row">
            <label style="display:flex;align-items:center;gap:10px;font-size:14px;">
                <input type="checkbox" id="divider-enabled" {"checked" if enabled else ""} style="width:18px;height:18px;">
                Enabled
            </label>
            <span class="status" id="enabled-status"></span>
        </div>
    </div>

    <div class="group">
        <h2>Image</h2>
        <div class="field">
            <label>Image URL</label>
            <div class="field-right">
                <input type="url" id="divider-image-url" placeholder="https://..." value="{image_url}" style="min-width:280px;">
            </div>
        </div>
        <div class="action-row">
            <span></span>
            <div class="field-right">
                <button class="btn" id="save-image-btn">Save image</button>
                <span class="status" id="image-status"></span>
            </div>
        </div>
        <div id="image-preview">{preview_html}</div>
    </div>

    <div class="group">
        <h2>Channels</h2>
        <div id="channel-list">{channel_rows}</div>
        <div class="action-row">
            <select id="add-channel-select">{add_channel_options}</select>
            <div class="field-right">
                <button class="btn" id="add-channel-btn">Add channel</button>
                <span class="status" id="add-channel-status"></span>
            </div>
        </div>
    </div>

    <script>const API_BASE = '/dashboard/{guild.id}/api';</script>
    <script>{DIVIDER_JS}</script>
    """
    body = _sidebar_shell(guild, _icon_url(guild), "divider", content)
    return web.Response(
        text=_page_shell(f"{guild.name} · Auto Divider", SIDEBAR_STYLES + SETTINGS_STYLES, body),
        content_type="text/html",
    )


async def security_page(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id)
    if not isinstance(guard, discord.Guild):
        return guard
    guild = guard

    settings = await get_guild_settings(guild_id)
    security = settings.get("security", {})
    anti_nuke = security.get("anti_nuke", {})
    anti_spam = security.get("anti_spam", {})
    anti_link = security.get("anti_link", {})
    word_filter = security.get("word_filter", {})

    log_channel_options = _build_options(guild, "channel", security.get("log_channel_id"))
    link_channel_options = _build_options(guild, "channel", None)

    def _chip_rows(values, remove_attr, label_fn=str):
        if not values:
            return '<p class="group-hint">None added yet.</p>'
        rows = ""
        for v in values:
            rows += f"""
            <div class="field">
                <label>{label_fn(v)}</label>
                <div class="field-right">
                    <button class="btn danger" data-{remove_attr}="{v}">Remove</button>
                </div>
            </div>
            """
        return rows

    whitelist_users = security.get("whitelist_user_ids", [])
    whitelist_rows = _chip_rows(
        whitelist_users, "remove-whitelist-user",
        lambda uid: (lambda m: f"{m} ({uid})" if m else f"Unknown user ({uid})")(guild.get_member(uid)),
    )
    banned_words_rows = _chip_rows(word_filter.get("banned_words", []), "remove-banned-word")
    link_domain_rows = _chip_rows(anti_link.get("whitelist_domains", []), "remove-link-domain")
    link_channel_rows = _chip_rows(
        anti_link.get("whitelist_channel_ids", []), "remove-link-channel",
        lambda cid: (lambda c: f"#{c.name}" if c else f"Unknown channel ({cid})")(guild.get_channel(cid)),
    )

    def _toggle_row(system_key, nice_name, enabled):
        return f"""
        <div class="action-row">
            <label style="display:flex;align-items:center;gap:10px;font-size:14px;">
                <input type="checkbox" class="sec-toggle" data-system="{system_key}" {"checked" if enabled else ""} style="width:18px;height:18px;">
                {nice_name}
            </label>
            <span class="status" id="{system_key}-toggle-status"></span>
        </div>
        """

    content = f"""
    <div class="eyebrow">Security</div>
    <h1>Protect this server automatically</h1>
    <p class="subtitle">
        Every sub-system below is off until you turn it on. Admins (Administrator permission) are always
        exempt from message-based checks (anti-spam / anti-link / word filter).
    </p>

    <div class="group">
        <h2>Alerts</h2>
        <div class="field">
            <label>Log Channel</label>
            <div class="field-right">
                <select id="sec-log-channel">{log_channel_options}</select>
            </div>
        </div>
        <div class="action-row">
            <span></span>
            <div class="field-right">
                <button class="btn" id="save-log-channel-btn">Save</button>
                <span class="status" id="log-channel-status"></span>
            </div>
        </div>
        <p class="group-hint">Falls back to the Warn Log channel if not set.</p>
    </div>

    <div class="group">
        <h2>Anti-Nuke</h2>
        {_toggle_row("anti_nuke", "Enabled", anti_nuke.get("enabled"))}
        <div class="field">
            <label>Action threshold</label>
            <div class="field-right"><input type="number" id="nuke-threshold" min="1" value="{anti_nuke.get('action_threshold', 3)}" style="width:80px;"></div>
        </div>
        <div class="field">
            <label>Window (seconds)</label>
            <div class="field-right"><input type="number" id="nuke-window" min="1" value="{anti_nuke.get('window_seconds', 10)}" style="width:80px;"></div>
        </div>
        <div class="field">
            <label>Punishment</label>
            <div class="field-right">
                <select id="nuke-punishment">
                    <option value="strip_roles" {"selected" if anti_nuke.get("punishment", "strip_roles") == "strip_roles" else ""}>Strip all roles</option>
                    <option value="ban" {"selected" if anti_nuke.get("punishment") == "ban" else ""}>Ban</option>
                </select>
            </div>
        </div>
        <div class="action-row">
            <span></span>
            <div class="field-right">
                <button class="btn" id="save-nuke-btn">Save</button>
                <span class="status" id="nuke-status"></span>
            </div>
        </div>
        <h3 style="margin-top:16px;font-size:14px;">Exempt users (immune to anti-nuke actions)</h3>
        <div id="whitelist-user-list">{whitelist_rows}</div>
        <div class="action-row">
            <input type="text" id="add-whitelist-user" placeholder="User ID" style="min-width:200px;">
            <div class="field-right">
                <button class="btn" id="add-whitelist-user-btn">Add</button>
                <span class="status" id="whitelist-user-status"></span>
            </div>
        </div>
    </div>

    <div class="group">
        <h2>Anti-Spam</h2>
        {_toggle_row("anti_spam", "Enabled", anti_spam.get("enabled"))}
        <div class="field">
            <label>Message threshold</label>
            <div class="field-right"><input type="number" id="spam-threshold" min="1" value="{anti_spam.get('message_threshold', 6)}" style="width:80px;"></div>
        </div>
        <div class="field">
            <label>Window (seconds)</label>
            <div class="field-right"><input type="number" id="spam-window" min="1" value="{anti_spam.get('window_seconds', 7)}" style="width:80px;"></div>
        </div>
        <div class="field">
            <label>Timeout (seconds)</label>
            <div class="field-right"><input type="number" id="spam-timeout" min="1" value="{anti_spam.get('timeout_seconds', 300)}" style="width:80px;"></div>
        </div>
        <div class="action-row">
            <span></span>
            <div class="field-right">
                <button class="btn" id="save-spam-btn">Save</button>
                <span class="status" id="spam-status"></span>
            </div>
        </div>
    </div>

    <div class="group">
        <h2>Anti-Link</h2>
        {_toggle_row("anti_link", "Enabled", anti_link.get("enabled"))}
        <h3 style="margin-top:16px;font-size:14px;">Whitelisted domains</h3>
        <div id="link-domain-list">{link_domain_rows}</div>
        <div class="action-row">
            <input type="text" id="add-link-domain" placeholder="e.g. tenor.com" style="min-width:200px;">
            <div class="field-right">
                <button class="btn" id="add-link-domain-btn">Add</button>
                <span class="status" id="link-domain-status"></span>
            </div>
        </div>
        <h3 style="margin-top:16px;font-size:14px;">Whitelisted channels (links always allowed)</h3>
        <div id="link-channel-list">{link_channel_rows}</div>
        <div class="action-row">
            <select id="add-link-channel-select">{link_channel_options}</select>
            <div class="field-right">
                <button class="btn" id="add-link-channel-btn">Add</button>
                <span class="status" id="link-channel-status"></span>
            </div>
        </div>
    </div>

    <div class="group">
        <h2>Word Filter</h2>
        {_toggle_row("word_filter", "Enabled", word_filter.get("enabled"))}
        <h3 style="margin-top:16px;font-size:14px;">Banned words</h3>
        <div id="banned-word-list">{banned_words_rows}</div>
        <div class="action-row">
            <input type="text" id="add-banned-word" placeholder="Word to block" style="min-width:200px;">
            <div class="field-right">
                <button class="btn" id="add-banned-word-btn">Add</button>
                <span class="status" id="banned-word-status"></span>
            </div>
        </div>
    </div>

    <script>const API_BASE = '/dashboard/{guild.id}/api';</script>
    <script>{SECURITY_JS}</script>
    """
    body = _sidebar_shell(guild, _icon_url(guild), "security", content)
    return web.Response(
        text=_page_shell(f"{guild.name} · Security", SIDEBAR_STYLES + SETTINGS_STYLES, body),
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

DIVIDER_JS = """
const enabledCheckbox = document.getElementById('divider-enabled');
enabledCheckbox.addEventListener('change', async function () {
    const statusEl = document.getElementById('enabled-status');
    try {
        const res = await fetch(API_BASE + '/divider/enabled', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: enabledCheckbox.checked }),
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

document.getElementById('save-image-btn').addEventListener('click', async function () {
    const url = document.getElementById('divider-image-url').value.trim();
    const statusEl = document.getElementById('image-status');
    try {
        const res = await fetch(API_BASE + '/divider/image', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image_url: url || null }),
        });
        if (!res.ok) throw new Error('save failed');
        statusEl.textContent = 'Saved';
        statusEl.className = 'status show ok';
        const preview = document.getElementById('image-preview');
        preview.innerHTML = url
            ? '<img src="' + url + '" alt="Divider preview" style="max-width:100%;border-radius:8px;border:1px solid var(--border);margin-top:10px;">'
            : '<p class="group-hint">No image set yet.</p>';
    } catch (e) {
        statusEl.textContent = 'Error';
        statusEl.className = 'status show err';
    }
    setTimeout(function () { statusEl.classList.remove('show'); }, 1500);
});

document.getElementById('add-channel-btn').addEventListener('click', async function () {
    const channelId = document.getElementById('add-channel-select').value;
    const statusEl = document.getElementById('add-channel-status');
    if (!channelId) {
        statusEl.textContent = 'Pick a channel';
        statusEl.className = 'status show err';
        setTimeout(function () { statusEl.classList.remove('show'); }, 1500);
        return;
    }
    try {
        const res = await fetch(API_BASE + '/divider/channels', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channel_id: channelId }),
        });
        if (!res.ok) throw new Error('save failed');
        location.reload();
    } catch (e) {
        statusEl.textContent = 'Error';
        statusEl.className = 'status show err';
        setTimeout(function () { statusEl.classList.remove('show'); }, 1500);
    }
});

document.querySelectorAll('[data-remove-channel]').forEach(function (btn) {
    btn.addEventListener('click', async function () {
        const channelId = btn.dataset.removeChannel;
        try {
            const res = await fetch(API_BASE + '/divider/channels/' + channelId, { method: 'DELETE' });
            if (!res.ok) throw new Error('remove failed');
            location.reload();
        } catch (e) {
            btn.textContent = 'Error';
        }
    });
});
"""

SECURITY_JS = """
function flashStatus(el, ok) {
    el.textContent = ok ? 'Saved' : 'Error';
    el.className = 'status show ' + (ok ? 'ok' : 'err');
    setTimeout(function () { el.classList.remove('show'); }, 1500);
}

document.querySelectorAll('.sec-toggle').forEach(function (box) {
    box.addEventListener('change', async function () {
        const system = box.dataset.system;
        const statusEl = document.getElementById(system + '-toggle-status');
        try {
            const res = await fetch(API_BASE + '/security/toggle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ system: system, enabled: box.checked }),
            });
            flashStatus(statusEl, res.ok);
        } catch (e) { flashStatus(statusEl, false); }
    });
});

document.getElementById('save-log-channel-btn').addEventListener('click', async function () {
    const channelId = document.getElementById('sec-log-channel').value;
    const statusEl = document.getElementById('log-channel-status');
    try {
        const res = await fetch(API_BASE + '/security/log-channel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channel_id: channelId || null }),
        });
        flashStatus(statusEl, res.ok);
    } catch (e) { flashStatus(statusEl, false); }
});

document.getElementById('save-nuke-btn').addEventListener('click', async function () {
    const statusEl = document.getElementById('nuke-status');
    try {
        const res = await fetch(API_BASE + '/security/anti-nuke', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                action_threshold: parseInt(document.getElementById('nuke-threshold').value, 10),
                window_seconds: parseInt(document.getElementById('nuke-window').value, 10),
                punishment: document.getElementById('nuke-punishment').value,
            }),
        });
        flashStatus(statusEl, res.ok);
    } catch (e) { flashStatus(statusEl, false); }
});

document.getElementById('save-spam-btn').addEventListener('click', async function () {
    const statusEl = document.getElementById('spam-status');
    try {
        const res = await fetch(API_BASE + '/security/anti-spam', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message_threshold: parseInt(document.getElementById('spam-threshold').value, 10),
                window_seconds: parseInt(document.getElementById('spam-window').value, 10),
                timeout_seconds: parseInt(document.getElementById('spam-timeout').value, 10),
            }),
        });
        flashStatus(statusEl, res.ok);
    } catch (e) { flashStatus(statusEl, false); }
});

function wireAddRemove(addBtnId, inputId, statusId, endpoint, payloadKey, removeAttr) {
    document.getElementById(addBtnId).addEventListener('click', async function () {
        const el = document.getElementById(inputId);
        const value = el.tagName === 'SELECT' ? el.value : el.value.trim();
        const statusEl = document.getElementById(statusId);
        if (!value) { flashStatus(statusEl, false); return; }
        try {
            const payload = {}; payload[payloadKey] = value;
            const res = await fetch(API_BASE + endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!res.ok) throw new Error('failed');
            location.reload();
        } catch (e) { flashStatus(statusEl, false); }
    });
    document.querySelectorAll('[data-' + removeAttr + ']').forEach(function (btn) {
        btn.addEventListener('click', async function () {
            const value = btn.getAttribute('data-' + removeAttr);
            try {
                const payload = { remove: true }; payload[payloadKey] = value;
                const res = await fetch(API_BASE + endpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                if (!res.ok) throw new Error('failed');
                location.reload();
            } catch (e) { btn.textContent = 'Error'; }
        });
    });
}

wireAddRemove('add-whitelist-user-btn', 'add-whitelist-user', 'whitelist-user-status', '/security/whitelist-user', 'user_id', 'remove-whitelist-user');
wireAddRemove('add-banned-word-btn', 'add-banned-word', 'banned-word-status', '/security/banned-word', 'word', 'remove-banned-word');
wireAddRemove('add-link-domain-btn', 'add-link-domain', 'link-domain-status', '/security/link-domain', 'domain', 'remove-link-domain');
wireAddRemove('add-link-channel-btn', 'add-link-channel-select', 'link-channel-status', '/security/link-channel', 'channel_id', 'remove-link-channel');
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
