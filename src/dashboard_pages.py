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

import datetime

import aiohttp
import discord
from aiohttp import web
from urllib.parse import urlencode

import config
import utils.paddle_billing as paddle_billing
from dashboard_core import (
    COOKIE_NAME,
    DISCORD_API,
    CATEGORY_COLORS,
    EMBED_BUILDER_STYLES,
    GUILD_LIST_STYLES,
    LANDING_STYLES,
    LEGAL_STYLES,
    LOG_GROUPS,
    OVERVIEW_STYLES,
    SETTINGS_GROUPS,
    SETTINGS_LABELS,
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
from utils.db import DEFAULT_SETTINGS, get_daily_stats, get_guild_settings, list_embed_drafts, list_licensed_guilds
from utils.chart_svg import bar_chart_svg, line_chart_svg
from utils.embed_builder import LIMITS
from utils.message_templates import TEMPLATE_SLOTS

_TODAY = datetime.date.today().strftime("%B %d, %Y")

# ---------------------------------------------------------
# OAuth routes
# ---------------------------------------------------------
async def login_start(request: web.Request) -> web.Response:
    """The actual OAuth kickoff - separated from login() (the marketing/landing
    page at /login) so landing there doesn't immediately bounce the person to
    Discord before they've seen anything."""
    params = {
        "client_id": config.DISCORD_CLIENT_ID,
        "redirect_uri": config.REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds",
    }
    return web.HTTPFound(f"{DISCORD_API}/oauth2/authorize?{urlencode(params)}")


def _legal_page_shell(title: str, body_html: str) -> str:
    """Shared wrapper for /terms, /privacy, /refund - public, no login required,
    no sidebar (not guild-scoped). Kept as plain readable text rather than
    cards/forms since these are documents, not app UI."""
    bot_name = config.BOT_NAME or "this bot"
    content = f"""
    <div class="topbar">
        <div class="brand"><span class="brand-dot"></span> {bot_name}</div>
        <div class="spacer"></div>
        <a class="link-btn" href="/login">Back</a>
    </div>
    <div class="container" style="max-width: 720px;">
        <div class="eyebrow">Legal</div>
        <h1>{title}</h1>
        <div class="legal-body">{body_html}</div>
    </div>
    """
    return _page_shell(f"{bot_name} · {title}", GUILD_LIST_STYLES + LEGAL_STYLES, content)


async def terms_page(request: web.Request) -> web.Response:
    bot_name = config.BOT_NAME or "this bot"
    body = f"""
    <p><em>Last updated: {_TODAY}</em></p>

    <h2>1. Acceptance of terms</h2>
    <p>By inviting {bot_name} to a Discord server, or by accessing its web dashboard, you agree to these Terms of Service. If you don't agree, don't use {bot_name}.</p>

    <h2>2. What {bot_name} is</h2>
    <p>{bot_name} is a Discord bot and companion web dashboard providing moderation, security, engagement, and community-management tools for Discord servers. A Free plan and a paid Pro plan are available; see the dashboard's Upgrade page for what each includes.</p>

    <h2>3. Eligibility</h2>
    <p>You must be able to form a binding contract to use {bot_name}, and must comply with Discord's own Terms of Service, including its minimum age requirement. Using {bot_name} to violate Discord's Terms of Service or Community Guidelines is a violation of these terms too.</p>

    <h2>4. Subscriptions and billing</h2>
    <p>The Pro plan is billed monthly on a recurring basis through Paddle.com, our payment processor and merchant of record. Paddle handles the actual charge, invoicing, and tax collection - {bot_name} never receives or stores your card details. You can cancel anytime from the dashboard's billing management page; Pro features remain active until the end of the current billing period. See our <a href="/refund">Refund Policy</a> for refund terms.</p>

    <h2>5. Acceptable use</h2>
    <p>You agree not to use {bot_name} to violate any law, harass or harm others, distribute malware, or attempt to abuse, reverse-engineer, or overload the service. We may suspend or terminate access for any account or server found doing so.</p>

    <h2>6. Availability</h2>
    <p>{bot_name} is provided on a best-effort basis. We don't guarantee uninterrupted availability and aren't liable for downtime, data loss, or moderation actions taken (or not taken) by the bot's automated systems.</p>

    <h2>7. Termination</h2>
    <p>You may stop using {bot_name} at any time by removing it from your server. We may suspend or terminate service to any server or account that violates these terms.</p>

    <h2>8. Limitation of liability</h2>
    <p>{bot_name} is provided "as is" without warranties of any kind. To the maximum extent permitted by law, we aren't liable for any indirect, incidental, or consequential damages arising from your use of the service.</p>

    <h2>9. Changes to these terms</h2>
    <p>We may update these terms from time to time. Continued use of {bot_name} after changes take effect constitutes acceptance of the updated terms.</p>

    <h2>10. Contact</h2>
    <p>Questions about these terms: <a href="mailto:{config.SUPPORT_EMAIL}">{config.SUPPORT_EMAIL}</a></p>
    """
    return web.Response(text=_legal_page_shell("Terms of Service", body), content_type="text/html")


async def privacy_page(request: web.Request) -> web.Response:
    bot_name = config.BOT_NAME or "this bot"
    body = f"""
    <p><em>Last updated: {_TODAY}</em></p>

    <h2>1. What we collect</h2>
    <p>
        <strong>From Discord (via OAuth login):</strong> your Discord user ID, username, avatar, and the list of servers you have Manage Server permission in - used only to show you the right servers in the dashboard and verify you're allowed to configure them.<br><br>
        <strong>Per-server settings:</strong> whatever you configure in the dashboard or via commands (channel IDs, role IDs, feature toggles, custom messages, embed drafts).<br><br>
        <strong>Message content:</strong> not stored permanently. Messages are read transiently by moderation/security features (e.g. word filter, anti-spam) to decide whether to act, and by the AI chat feature (sent to our AI provider to generate a reply - see below). Brief summaries of moderation/security actions (e.g. "message deleted for banned word") are kept in the searchable Log History feature, but not full message contents.<br><br>
        <strong>Leveling data:</strong> your Discord user ID and accumulated XP per server, to power leveling and leaderboards.<br><br>
        <strong>Billing:</strong> we never see or store your card details. Payments are handled entirely by Paddle.com; we only store your Paddle customer/subscription ID and plan status.
    </p>

    <h2>2. How we use it</h2>
    <p>To operate the bot and dashboard, enforce the settings you configure, provide AI chat replies, process subscription payments, and improve reliability. We don't sell your data, and we don't use message content for advertising.</p>

    <h2>3. Third parties we share data with</h2>
    <p>
        <strong>Discord:</strong> all core functionality runs through Discord's API.<br>
        <strong>Paddle.com:</strong> processes all payments as our merchant of record.<br>
        <strong>OpenRouter (AI provider):</strong> if your server enables AI chat, the message you send is transmitted to OpenRouter to generate a reply.<br>
        <strong>MongoDB Atlas:</strong> our database host, where server settings and the data described above are stored.
    </p>

    <h2>4. Data retention and deletion</h2>
    <p>Server settings are kept as long as {bot_name} remains in your server. Removing the bot from your server does not automatically delete stored data; email <a href="mailto:{config.SUPPORT_EMAIL}">{config.SUPPORT_EMAIL}</a> to request deletion of your server's or your own data at any time.</p>

    <h2>5. Children's privacy</h2>
    <p>{bot_name} is only intended for use in compliance with Discord's own Terms of Service, which requires users to meet Discord's minimum age requirement. We don't knowingly collect data from anyone who doesn't meet that requirement.</p>

    <h2>6. Changes to this policy</h2>
    <p>We may update this policy from time to time; the "Last updated" date above will reflect the most recent change.</p>

    <h2>7. Contact</h2>
    <p>Privacy questions or data requests: <a href="mailto:{config.SUPPORT_EMAIL}">{config.SUPPORT_EMAIL}</a></p>
    """
    return web.Response(text=_legal_page_shell("Privacy Policy", body), content_type="text/html")


async def refund_page(request: web.Request) -> web.Response:
    body = f"""
    <p><em>Last updated: {_TODAY}</em></p>

    <h2>7-day money-back guarantee</h2>
    <p>If you're a first-time Pro subscriber and aren't satisfied, contact us within 7 days of your initial payment for a full refund - no questions asked.</p>

    <h2>After the first 7 days</h2>
    <p>Subscriptions renew monthly. You can cancel anytime from the dashboard's billing page (Upgrade → Manage billing) - Pro features stay active until the end of the period you already paid for, but we don't offer partial-month refunds for time already used within a billing period.</p>

    <h2>Billing errors</h2>
    <p>If you were charged in error (e.g. duplicate charge, charged after cancelling), contact us and we'll refund it in full - this isn't subject to the 7-day window above.</p>

    <h2>How to request a refund</h2>
    <p>Email <a href="mailto:{config.SUPPORT_EMAIL}">{config.SUPPORT_EMAIL}</a> with the Discord server name and the email address used at checkout. Since Paddle.com processes our payments as merchant of record, refunds are issued through Paddle and typically appear on your statement within 5-10 business days.</p>
    """
    return web.Response(text=_legal_page_shell("Refund Policy", body), content_type="text/html")


async def pricing_page(request: web.Request) -> web.Response:
    """Public pricing page - no login required. Exists specifically so an
    outside reviewer (e.g. Paddle's website approval process) or a prospective
    customer can see actual prices without signing in with Discord first -
    the dashboard's /dashboard/{guild_id}/upgrade page shows the same numbers
    but sits behind OAuth + Manage Server permission, so it doesn't count as
    a publicly visible pricing page."""
    bot_name = config.BOT_NAME or "Bot Dashboard"
    price = config.PRO_PRICE_DISPLAY
    content = f"""
    <div class="topbar">
        <div class="brand"><span class="brand-dot"></span> {bot_name}</div>
        <div class="spacer"></div>
        <a class="link-btn" href="/login">Sign in</a>
    </div>
    <div class="landing-hero" style="padding-top:56px;">
        <div class="eyebrow">Pricing</div>
        <h1>Simple, two-tier pricing</h1>
        <p class="subtitle">Free covers a full-featured community bot. Pro adds serious protection and premium extras. Cancel anytime.</p>
    </div>
    <div class="container" style="max-width: 840px; padding-top: 0;">
        <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px;">
            <div class="group" style="margin-bottom:0;">
                <h2>Free</h2>
                <p class="group-hint" style="font-size:24px;color:var(--ink);margin:0 0 16px;font-family:var(--font-display);">$0<span style="font-size:13px;color:var(--ink-dim);"> forever</span></p>
                <p class="group-hint">Moderation (kick/ban/mute/warn)</p>
                <p class="group-hint">Tickets, welcome messages, auto-role</p>
                <p class="group-hint">Full custom embed builder</p>
                <p class="group-hint">AI chat</p>
                <p class="group-hint">Logs</p>
                <p class="group-hint">Leveling with XP, rank cards, and role rewards</p>
                <p class="group-hint">Reaction roles</p>
            </div>
            <div class="group" style="margin-bottom:0; border-color: var(--signal); border-width: 2px;">
                <h2 style="color: var(--signal);">Pro</h2>
                <p class="group-hint" style="font-size:24px;color:var(--ink);margin:0 0 16px;font-family:var(--font-display);">{price}</p>
                <p class="group-hint">Everything in Free, plus:</p>
                <p class="group-hint">Full security suite - anti-nuke, anti-spam, anti-link, word filter, anti-webhook, raid mode</p>
                <p class="group-hint">Private member-owned voice rooms</p>
                <p class="group-hint">Music</p>
                <p class="group-hint">White-label dashboard branding</p>
                <p class="group-hint">Advanced analytics and searchable log history</p>
            </div>
        </div>
        <p class="group-hint" style="margin-top:20px;">Billed monthly, cancel anytime. See our <a href="/refund">Refund Policy</a> for details. Payments processed securely by <a href="https://paddle.com" target="_blank" rel="noopener">Paddle.com</a>, our merchant of record.</p>
    </div>
    """
    return web.Response(
        text=_page_shell(f"{bot_name} · Pricing", GUILD_LIST_STYLES + LANDING_STYLES + SETTINGS_STYLES, content),
        content_type="text/html",
    )


async def login(request: web.Request) -> web.Response:
    """Landing page at /login - shown to anyone not signed in yet (first visit,
    expired session, or after logout), with a feature overview and a single
    CTA into login_start()."""
    bot_name = config.BOT_NAME or "Bot Dashboard"
    features = [
        ("core", "Core", "Moderation, tickets, welcome messages, and a fully custom embed builder."),
        ("security", "Security", "Anti-nuke, anti-spam, anti-link, word filtering, raid mode, and more - off until you turn it on."),
        ("engagement", "Engagement", "XP leveling with rank cards, reaction roles, and level-based role rewards."),
        ("community", "Community", "Member-owned private voice rooms, created on demand and cleaned up automatically."),
    ]
    feature_cards = "\n".join(
        f"""<div class="landing-feature" style="--rail-color: {CATEGORY_COLORS.get(cat, 'var(--signal)')};">
            <div class="landing-feature-title">{title}</div>
            <div class="landing-feature-desc">{desc}</div>
        </div>"""
        for cat, title, desc in features
    )

    content = f"""
    <div class="topbar">
        <div class="brand"><span class="brand-dot"></span> {bot_name}</div>
    </div>
    <div class="landing-hero">
        <div class="eyebrow">Discord bot &amp; dashboard</div>
        <h1>Run a sharper server with {bot_name}</h1>
        <p class="subtitle">Security, engagement, and community tools in one bot - configured from a dashboard built for admins, not developers.</p>
        <a class="btn" href="/login/start" style="display:inline-block;text-decoration:none;padding:12px 24px;font-size:14px;">Continue with Discord</a>
    </div>
    <div class="landing-features">
        {feature_cards}
    </div>
    <div style="text-align:center;padding:24px 16px 60px;">
        <a href="/pricing" class="link-btn" style="border:none;">Pricing</a>
        <a href="/terms" class="link-btn" style="border:none;">Terms of Service</a>
        <a href="/privacy" class="link-btn" style="border:none;">Privacy Policy</a>
        <a href="/refund" class="link-btn" style="border:none;">Refund Policy</a>
    </div>
    """
    return web.Response(text=_page_shell(f"{bot_name} · Sign in", GUILD_LIST_STYLES + LANDING_STYLES + SETTINGS_STYLES, content), content_type="text/html")


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
    response = web.HTTPFound("/login")
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


def _is_bot_owner(session_data: dict, bot) -> bool:
    user = session_data.get("user")
    if not user:
        return False
    user_id = str(user.get("id"))
    if bot.owner_id is not None and str(bot.owner_id) == user_id:
        return True
    if bot.owner_ids:
        return user_id in {str(x) for x in bot.owner_ids}
    return False


async def admin_page(request: web.Request, bot) -> web.Response:
    """Owner-only overview of every guild with a non-default license state -
    who's on Pro, who has a payment issue, who's on a manually-granted
    unlimited plan. Not linked from anywhere in the regular per-guild
    dashboard; only reachable by the bot owner navigating to /admin directly."""
    session_data = _read_session(request)
    if not _is_bot_owner(session_data, bot):
        return web.Response(text="Not found.", status=404)

    docs = await list_licensed_guilds()
    rows = ""
    for doc in docs:
        guild = bot.get_guild(doc["guild_id"])
        name = guild.name if guild else f"Unknown ({doc['guild_id']})"
        license_info = doc.get("license", {})
        plan = license_info.get("plan", "free")
        payment_issue = license_info.get("payment_issue", False)
        has_paddle = bool(license_info.get("paddle_subscription_id"))

        plan_badge = {"pro": "⭐ Pro", "unlimited": "♾️ Unlimited", "free": "Free"}.get(plan, plan)
        issue_badge = ' <span style="color: var(--danger);">⚠ payment issue</span>' if payment_issue else ""
        source_badge = " (Paddle)" if has_paddle else " (manual)" if plan != "free" else ""

        rows += f"""
        <div class="field">
            <label>{name} <span class="group-hint" style="margin:0;">({doc['guild_id']})</span></label>
            <div class="field-right">{plan_badge}{source_badge}{issue_badge}</div>
        </div>
        """

    pro_count = sum(1 for d in docs if d.get("license", {}).get("plan") == "pro")
    issue_count = sum(1 for d in docs if d.get("license", {}).get("payment_issue"))

    content = f"""
    <div class="topbar">
        <div class="brand"><span class="brand-dot"></span> Bot Dashboard · Admin</div>
        <div class="spacer"></div>
        <a class="link-btn" href="/dashboard">Back to dashboard</a>
    </div>
    <div class="container" style="max-width: 820px;">
        <div class="eyebrow">Owner only</div>
        <h1>Billing overview</h1>
        <div class="stat-grid">
            <div class="stat-card"><div class="stat-value">{pro_count}</div><div class="stat-label">Guilds on Pro</div></div>
            <div class="stat-card"><div class="stat-value">{issue_count}</div><div class="stat-label">Payment issues</div></div>
            <div class="stat-card"><div class="stat-value">{len(docs)}</div><div class="stat-label">Total licensed guilds</div></div>
        </div>
        <div class="group">
            <h2>Guilds</h2>
            {rows or '<p class="group-hint">No guild has touched billing yet.</p>'}
        </div>
    </div>
    """
    return web.Response(
        text=_page_shell("Admin · Billing", GUILD_LIST_STYLES + SETTINGS_STYLES + OVERVIEW_STYLES, content),
        content_type="text/html",
    )


def _build_options(guild: discord.Guild, kind: str, current_value):
    """Builds <option> tags for a role / text-channel / voice-channel / category
    select, filtering out @everyone / managed (bot, booster, integration) roles,
    matching /setup's behavior."""
    options = ['<option value="">— Not set —</option>']
    if kind == "role":
        items = [r for r in guild.roles if not r.managed and r.name != "@everyone"]
        items.sort(key=lambda r: r.position, reverse=True)
    elif kind == "voice":
        items = list(guild.voice_channels)
        items.sort(key=lambda c: c.position)
    elif kind == "category":
        items = list(guild.categories)
        items.sort(key=lambda c: c.position)
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
    daily_stats = await get_daily_stats(guild_id, days=14)

    member_series = [d["member_count_snapshot"] or 0 for d in daily_stats]
    message_series = [d["messages"] for d in daily_stats]
    has_history = any(d["member_count_snapshot"] is not None for d in daily_stats)

    if has_history:
        # Backfill any leading days before the bot started tracking with the
        # first known snapshot, so the line doesn't dip to zero at the start.
        first_known = next((v for v in member_series if v), guild.member_count or 0)
        member_series = [v or first_known for v in member_series]
        charts_html = f"""
        <div class="group">
            <h2>Member growth (14 days)</h2>
            {line_chart_svg(member_series, color="#F0A94E")}
        </div>
        <div class="group">
            <h2>Messages per day (14 days)</h2>
            {bar_chart_svg(message_series, color="#9A87F0")}
        </div>
        """
    else:
        charts_html = """
        <div class="group">
            <h2>Activity</h2>
            <p class="group-hint">Charts appear here once the bot has been running for a day or two - come back soon.</p>
        </div>
        """

    content = f"""
    <div class="eyebrow">Server</div>
    <h1>{guild.name}</h1>
    <p class="subtitle">Everything here saves instantly and stays in sync with /setup in Discord.</p>

    <div class="stat-grid">
        <div class="stat-card"><div class="stat-value">{guild.member_count or '—'}</div><div class="stat-label">Members</div></div>
        <div class="stat-card"><div class="stat-value">{configured_count}/{len(VALID_KEYS)}</div><div class="stat-label">Settings configured</div></div>
        <div class="stat-card"><div class="stat-value">{len(drafts)}</div><div class="stat-label">Saved embed drafts</div></div>
    </div>

    {charts_html}

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
    body = _sidebar_shell(guild, _icon_url(guild), "overview", content, branding=settings.get("dashboard_branding", {}))
    return web.Response(text=_page_shell(f"{guild.name} · Overview", SIDEBAR_STYLES + OVERVIEW_STYLES, body), content_type="text/html")


async def upgrade_page(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id)
    if not isinstance(guard, discord.Guild):
        return guard
    guild = guard

    settings = await get_guild_settings(guild_id)
    license_info = settings.get("license", {})
    plan = license_info.get("plan", "free")
    payment_issue = license_info.get("payment_issue", False)
    has_subscription = bool(license_info.get("paddle_subscription_id"))
    paddle_ready = paddle_billing.is_configured() and bool(config.PADDLE_CLIENT_TOKEN)

    issue_banner = ""
    if payment_issue:
        issue_banner = """
        <div class="group" style="border-color: var(--danger); border-left: 3px solid var(--danger);">
            <h2 style="color: var(--danger);">Payment issue</h2>
            <p class="group-hint" style="margin-bottom: 0;">
                Paddle couldn't process your last payment and is retrying automatically.
                Pro features stay active while it retries - use "Manage billing" below to update your card.
            </p>
        </div>
        """

    if plan in ("pro", "unlimited"):
        status_html = f"""
        {issue_banner}
        <div class="group">
            <h2>You're on Pro{"" if plan == "pro" else " (internal)"}</h2>
            <p class="group-hint">Every Pro feature is unlocked for this server.</p>
            {'<div class="action-row"><span></span><div class="field-right"><button class="btn secondary" id="manage-billing-btn">Manage billing</button><span class="status" id="upgrade-status"></span></div></div>' if has_subscription else ''}
        </div>
        """
    else:
        price = config.PRO_PRICE_DISPLAY
        status_html = f"""
        <div class="landing-features" style="padding:0 0 4px;margin:0;">
            <div class="landing-feature" style="--rail-color: var(--cat-core);">
                <div class="landing-feature-title">Free - current plan</div>
                <div class="landing-feature-desc">Moderation, tickets, welcome, embed builder, logs, AI chat, full leveling with role rewards, and reaction roles.</div>
            </div>
            <div class="landing-feature" style="--rail-color: var(--signal); border-width: 2px;">
                <div class="landing-feature-title">Pro - {price}</div>
                <div class="landing-feature-desc">Everything in Free, plus the full security suite (anti-nuke, anti-spam, anti-link, word filter, anti-webhook, raid mode), private voice rooms, music, and white-label dashboard branding.</div>
            </div>
        </div>
        <div class="group">
            <div class="action-row">
                <span></span>
                <div class="field-right">
                    <button class="btn" id="upgrade-btn" {"disabled" if not paddle_ready else ""}>Upgrade to Pro - {price}</button>
                    <span class="status" id="upgrade-status"></span>
                </div>
            </div>
            {'<p class="group-hint">Billing isn\'t configured on this deployment yet.</p>' if not paddle_ready else ''}
        </div>
        """

    content = f"""
    <div class="eyebrow">Billing</div>
    <h1>Upgrade to Pro</h1>
    <p class="subtitle">Secure checkout via Paddle. Cancel anytime - Pro features stay active until the end of the billing period.</p>
    {status_html}
    <script src="https://cdn.paddle.com/paddle/v2/paddle.js"></script>
    <script>
        const API_BASE = '/dashboard/{guild.id}/api';
        const PADDLE_CLIENT_TOKEN = {config.PADDLE_CLIENT_TOKEN!r};
        const PADDLE_ENV = {config.PADDLE_ENVIRONMENT!r};
    </script>
    <script>{UPGRADE_JS}</script>
    """
    body = _sidebar_shell(guild, _icon_url(guild), "upgrade", content, branding=settings.get("dashboard_branding", {}))
    return web.Response(text=_page_shell(f"{guild.name} · Upgrade", SIDEBAR_STYLES + SETTINGS_STYLES + LANDING_STYLES, body), content_type="text/html")


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
    body = _sidebar_shell(guild, _icon_url(guild), "settings", content, branding=settings.get("dashboard_branding", {}))
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
    body = _sidebar_shell(guild, _icon_url(guild), "logs", content, branding=settings.get("dashboard_branding", {}))
    return web.Response(text=_page_shell(f"{guild.name} · Logs", SIDEBAR_STYLES + SETTINGS_STYLES, body), content_type="text/html")


async def log_history_page(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id)
    if not isinstance(guard, discord.Guild):
        return guard
    guild = guard

    settings = await get_guild_settings(guild_id)
    category_labels = {**SETTINGS_LABELS, "security_log_channel_id": "Security Alerts"}
    log_setting_keys = [key for _, fields in LOG_GROUPS for key, *_ in fields] + ["security_log_channel_id"]
    category_options = '<option value="">All categories</option>' + "\n".join(
        f'<option value="{key}">{category_labels.get(key, key)}</option>' for key in log_setting_keys
    )

    content = f"""
    <div class="eyebrow">Logs</div>
    <h1>Log history</h1>
    <p class="subtitle">
        Every event ever sent to a log channel, searchable - even ones sent before the channel was configured.
        Pulled from the same events shown in Discord, just easier to search back through.
    </p>

    <div class="group" style="padding-bottom:22px;">
        <div class="action-row" style="padding-top:0;">
            <input type="text" id="lh-search" placeholder="Search titles and details…" style="flex:1;min-width:0;">
            <select id="lh-category">{category_options}</select>
        </div>
    </div>

    <div id="lh-results"><p class="group-hint">Loading…</p></div>

    <script>const API_BASE = '/dashboard/{guild.id}/api';</script>
    <script>{LOG_HISTORY_JS}</script>
    """
    body = _sidebar_shell(guild, _icon_url(guild), "log-history", content, branding=settings.get("dashboard_branding", {}))
    return web.Response(text=_page_shell(f"{guild.name} · Log History", SIDEBAR_STYLES + SETTINGS_STYLES, body), content_type="text/html")


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
    dash_branding = settings.get("dashboard_branding", {})
    dash_name_value = dash_branding.get("product_name") or ""
    dash_logo_value = dash_branding.get("logo_url") or ""
    dash_accent_value = dash_branding.get("accent_hex") or "#f0a94e"

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

    <div class="eyebrow" style="margin-top:8px;">White-label</div>
    <h1 style="font-size:20px;">This dashboard's own appearance</h1>
    <p class="subtitle">
        Rebrand the dashboard chrome itself (sidebar name, logo, accent color) - useful if you're reselling
        this bot under your own product name to a client. Leave blank to keep the default look.
    </p>
    <div class="group">
        <div class="field">
            <label>Product name</label>
            <div class="field-right"><input type="text" id="dash-brand-name" placeholder="Bot Dashboard" value="{dash_name_value}"></div>
        </div>
        <div class="field">
            <label>Logo URL (sidebar mark)</label>
            <div class="field-right"><input type="url" id="dash-brand-logo" placeholder="https://..." value="{dash_logo_value}"></div>
        </div>
        <div class="field">
            <label>Accent color</label>
            <div class="field-right"><input type="color" id="dash-brand-accent" value="{dash_accent_value}"></div>
        </div>
        <div class="action-row">
            <span></span>
            <div class="field-right">
                <button class="btn" id="dash-brand-save">Save white-label</button>
                <span class="status" id="dash-brand-status"></span>
            </div>
        </div>
    </div>

    <script>const API_BASE = '/dashboard/{guild.id}/api';</script>
    <script>{BRANDING_JS}</script>
    """
    body = _sidebar_shell(guild, _icon_url(guild), "branding", content, branding=settings.get("dashboard_branding", {}))
    return web.Response(text=_page_shell(f"{guild.name} · Branding", SIDEBAR_STYLES + SETTINGS_STYLES, body), content_type="text/html")


async def tickets_page(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id)
    if not isinstance(guard, discord.Guild):
        return guard
    guild = guard

    settings = await get_guild_settings(guild_id)
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
    body = _sidebar_shell(guild, _icon_url(guild), "tickets", content, branding=settings.get("dashboard_branding", {}))
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
    body = _sidebar_shell(guild, _icon_url(guild), "templates", content, branding=settings.get("dashboard_branding", {}))
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
    body = _sidebar_shell(guild, _icon_url(guild), "divider", content, branding=settings.get("dashboard_branding", {}))
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
    anti_webhook = security.get("anti_webhook", {})
    raid_mode = security.get("raid_mode", {})

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

    <div class="group">
        <h2>Anti-Webhook</h2>
        {_toggle_row("anti_webhook", "Enabled", anti_webhook.get("enabled"))}
        <p class="group-hint">Deletes any webhook created by a non-exempt member and punishes the creator - a common raid/phishing vector even after the attacker is kicked.</p>
        <div class="field">
            <label>Punishment</label>
            <div class="field-right">
                <select id="webhook-punishment">
                    <option value="strip_roles" {"selected" if anti_webhook.get("punishment", "strip_roles") == "strip_roles" else ""}>Strip all roles</option>
                    <option value="ban" {"selected" if anti_webhook.get("punishment") == "ban" else ""}>Ban</option>
                </select>
            </div>
        </div>
        <div class="action-row">
            <span></span>
            <div class="field-right">
                <button class="btn" id="save-webhook-btn">Save</button>
                <span class="status" id="webhook-status"></span>
            </div>
        </div>
    </div>

    <div class="group">
        <h2>Raid Mode</h2>
        {_toggle_row("raid_mode", "Enabled", raid_mode.get("enabled"))}
        <div class="field">
            <label>Join threshold</label>
            <div class="field-right"><input type="number" id="raid-threshold" min="1" value="{raid_mode.get('join_threshold', 5)}" style="width:80px;"></div>
        </div>
        <div class="field">
            <label>Window (seconds)</label>
            <div class="field-right"><input type="number" id="raid-window" min="1" value="{raid_mode.get('window_seconds', 10)}" style="width:80px;"></div>
        </div>
        <div class="field">
            <label>Response</label>
            <div class="field-right">
                <select id="raid-action">
                    <option value="lockdown" {"selected" if raid_mode.get("action", "lockdown") == "lockdown" else ""}>Raise verification level temporarily</option>
                    <option value="kick_new_accounts" {"selected" if raid_mode.get("action") == "kick_new_accounts" else ""}>Kick new accounts joining during the burst</option>
                </select>
            </div>
        </div>
        <div class="field">
            <label>Lockdown/response duration (minutes)</label>
            <div class="field-right"><input type="number" id="raid-duration" min="1" value="{raid_mode.get('lockdown_duration_minutes', 15)}" style="width:80px;"></div>
        </div>
        <div class="field">
            <label>Min. account age to allow (hours) - "kick" mode only</label>
            <div class="field-right"><input type="number" id="raid-min-age" min="0" value="{raid_mode.get('min_account_age_hours', 24)}" style="width:80px;"></div>
        </div>
        <div class="action-row">
            <span></span>
            <div class="field-right">
                <button class="btn" id="save-raid-btn">Save</button>
                <span class="status" id="raid-status"></span>
            </div>
        </div>
    </div>

    <script>const API_BASE = '/dashboard/{guild.id}/api';</script>
    <script>{SECURITY_JS}</script>
    """
    body = _sidebar_shell(guild, _icon_url(guild), "security", content, branding=settings.get("dashboard_branding", {}))
    return web.Response(
        text=_page_shell(f"{guild.name} · Security", SIDEBAR_STYLES + SETTINGS_STYLES, body),
        content_type="text/html",
    )


async def leveling_page(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id)
    if not isinstance(guard, discord.Guild):
        return guard
    guild = guard

    settings = await get_guild_settings(guild_id)
    leveling = settings.get("leveling", {})

    announce_channel_options = _build_options(guild, "channel", leveling.get("announce_channel_id"))
    ignored_channel_options = _build_options(guild, "channel", None)
    level_role_select_options = _build_options(guild, "role", None)

    def _ignored_channel_rows():
        ids = leveling.get("ignored_channel_ids", [])
        if not ids:
            return '<p class="group-hint">None - XP is earned in every channel.</p>'
        rows = ""
        for cid in ids:
            channel = guild.get_channel(cid)
            label = f"#{channel.name}" if channel else f"Unknown channel ({cid})"
            rows += f"""
            <div class="field">
                <label>{label}</label>
                <div class="field-right"><button class="btn danger" data-remove-ignored-channel="{cid}">Remove</button></div>
            </div>
            """
        return rows

    def _level_role_rows():
        mapping = leveling.get("level_roles", {})
        if not mapping:
            return '<p class="group-hint">No level-role rewards set yet.</p>'
        rows = ""
        for level_str, role_id in sorted(mapping.items(), key=lambda kv: int(kv[0])):
            role = guild.get_role(role_id)
            label = f"@{role.name}" if role else f"Unknown role ({role_id})"
            rows += f"""
            <div class="field">
                <label>Level {level_str} → {label}</label>
                <div class="field-right"><button class="btn danger" data-remove-level-role="{level_str}">Remove</button></div>
            </div>
            """
        return rows

    content = f"""
    <div class="eyebrow">Leveling</div>
    <h1>Reward active members automatically</h1>
    <p class="subtitle">Members earn XP for messages (with a cooldown so it can't be farmed) and level up over time.</p>

    <div class="group">
        <h2>General</h2>
        <div class="action-row">
            <label style="display:flex;align-items:center;gap:10px;font-size:14px;">
                <input type="checkbox" id="leveling-toggle" {"checked" if leveling.get("enabled") else ""} style="width:18px;height:18px;">
                Enabled
            </label>
            <span class="status" id="leveling-toggle-status"></span>
        </div>
        <div class="field">
            <label>XP per message (min / max)</label>
            <div class="field-right">
                <input type="number" id="xp-min" min="1" value="{leveling.get('xp_min', 15)}" style="width:70px;">
                <input type="number" id="xp-max" min="1" value="{leveling.get('xp_max', 25)}" style="width:70px;">
            </div>
        </div>
        <div class="field">
            <label>Cooldown (seconds)</label>
            <div class="field-right"><input type="number" id="xp-cooldown" min="1" value="{leveling.get('cooldown_seconds', 60)}" style="width:80px;"></div>
        </div>
        <div class="field">
            <label>Announce channel</label>
            <div class="field-right"><select id="announce-channel">{announce_channel_options}</select></div>
        </div>
        <div class="field">
            <label>Announce message</label>
            <div class="field-right">
                <textarea id="announce-message" rows="2" style="min-width:320px;">{leveling.get('announce_message', DEFAULT_SETTINGS['leveling']['announce_message'])}</textarea>
            </div>
        </div>
        <p class="group-hint">Use <code>{{member_mention}}</code> and <code>{{level}}</code> as placeholders. Leave the announce channel unset to announce in the channel the level-up happened in.</p>
        <div class="action-row">
            <span></span>
            <div class="field-right">
                <button class="btn" id="save-leveling-config-btn">Save</button>
                <span class="status" id="leveling-config-status"></span>
            </div>
        </div>
    </div>

    <div class="group">
        <h2>Ignored channels (no XP earned here)</h2>
        <div id="ignored-channel-list">{_ignored_channel_rows()}</div>
        <div class="action-row">
            <select id="add-ignored-channel-select">{ignored_channel_options}</select>
            <div class="field-right">
                <button class="btn" id="add-ignored-channel-btn">Add</button>
                <span class="status" id="ignored-channel-status"></span>
            </div>
        </div>
    </div>

    <div class="group">
        <h2>Level role rewards</h2>
        <div id="level-role-list">{_level_role_rows()}</div>
        <div class="action-row">
            <input type="number" id="add-level-role-level" min="1" placeholder="Level" style="width:80px;">
            <select id="add-level-role-select">{level_role_select_options}</select>
            <div class="field-right">
                <button class="btn" id="add-level-role-btn">Add</button>
                <span class="status" id="level-role-status"></span>
            </div>
        </div>
    </div>

    <script>const API_BASE = '/dashboard/{guild.id}/api';</script>
    <script>{LEVELING_JS}</script>
    """
    body = _sidebar_shell(guild, _icon_url(guild), "leveling", content, branding=settings.get("dashboard_branding", {}))
    return web.Response(
        text=_page_shell(f"{guild.name} · Leveling", SIDEBAR_STYLES + SETTINGS_STYLES, body),
        content_type="text/html",
    )


async def voice_rooms_page(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id)
    if not isinstance(guard, discord.Guild):
        return guard
    guild = guard

    settings = await get_guild_settings(guild_id)
    voice_rooms = settings.get("voice_rooms", {})

    hub_options = _build_options(guild, "voice", voice_rooms.get("hub_channel_id"))
    category_options = _build_options(guild, "category", voice_rooms.get("category_id"))

    content = f"""
    <div class="eyebrow">Voice Rooms</div>
    <h1>Give every member their own private room</h1>
    <p class="subtitle">
        Members join the hub channel below, and the bot instantly creates a private voice room for them -
        they get full control over it (lock, rename, limit, kick, permit, block) via <code>/room</code> commands.
        The room disappears automatically once it's empty.
    </p>

    <div class="group">
        <h2>General</h2>
        <div class="action-row">
            <label style="display:flex;align-items:center;gap:10px;font-size:14px;">
                <input type="checkbox" id="vr-toggle" {"checked" if voice_rooms.get("enabled") else ""} style="width:18px;height:18px;">
                Enabled
            </label>
            <span class="status" id="vr-toggle-status"></span>
        </div>
        <div class="field">
            <label>Hub voice channel ("join to create")</label>
            <div class="field-right"><select id="vr-hub-channel">{hub_options}</select></div>
        </div>
        <div class="field">
            <label>Category for new rooms</label>
            <div class="field-right"><select id="vr-category">{category_options}</select></div>
        </div>
        <div class="field">
            <label>Room name template</label>
            <div class="field-right"><input type="text" id="vr-name-template" value="{voice_rooms.get('name_template', "{{username}}'s Room")}" style="min-width:260px;"></div>
        </div>
        <div class="field">
            <label>Default user limit (0 = unlimited)</label>
            <div class="field-right"><input type="number" id="vr-default-limit" min="0" max="99" value="{voice_rooms.get('default_user_limit', 0)}" style="width:80px;"></div>
        </div>
        <p class="group-hint">Use <code>{{username}}</code> in the name template - it's replaced with the member's display name.</p>
        <div class="action-row">
            <span></span>
            <div class="field-right">
                <button class="btn" id="save-vr-btn">Save</button>
                <span class="status" id="vr-status"></span>
            </div>
        </div>
    </div>

    <script>const API_BASE = '/dashboard/{guild.id}/api';</script>
    <script>{VOICE_ROOMS_JS}</script>
    """
    body = _sidebar_shell(guild, _icon_url(guild), "voice-rooms", content, branding=settings.get("dashboard_branding", {}))
    return web.Response(
        text=_page_shell(f"{guild.name} · Voice Rooms", SIDEBAR_STYLES + SETTINGS_STYLES, body),
        content_type="text/html",
    )



async def embeds_page(request: web.Request, bot) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    access_token, guard = await _guarded_guild(request, bot, guild_id)
    if not isinstance(guard, discord.Guild):
        return guard
    guild = guard

    settings = await get_guild_settings(guild_id)
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
    body = _sidebar_shell(guild, _icon_url(guild), "embeds", content, wide=True, branding=settings.get("dashboard_branding", {}))
    return web.Response(
        text=_page_shell(f"{guild.name} · Embed Builder", SIDEBAR_STYLES + SETTINGS_STYLES + EMBED_BUILDER_STYLES, body),
        content_type="text/html",
    )


# ---------------------------------------------------------
# JS - one small shared save-pattern per page, plus the larger embed builder script
# ---------------------------------------------------------
UPGRADE_JS = """
if (PADDLE_CLIENT_TOKEN) {
    Paddle.Environment.set(PADDLE_ENV === 'sandbox' ? 'sandbox' : 'production');
    Paddle.Initialize({
        token: PADDLE_CLIENT_TOKEN,
        eventCallback: function (evt) {
            if (evt.name === 'checkout.completed') {
                // Actual license activation happens via the webhook (server-side,
                // reliable even if this tab closes) - this just improves perceived
                // responsiveness by refreshing the page once Paddle confirms locally.
                setTimeout(function () { window.location.href = window.location.pathname; }, 1200);
            }
        },
    });
}

const upgradeBtn = document.getElementById('upgrade-btn');
if (upgradeBtn) {
    upgradeBtn.addEventListener('click', async function () {
        const statusEl = document.getElementById('upgrade-status');
        upgradeBtn.disabled = true;
        statusEl.textContent = 'Opening checkout…';
        statusEl.className = 'status show ok';
        try {
            const res = await fetch(API_BASE + '/billing/create-checkout-session', { method: 'POST' });
            const data = await res.json();
            if (!res.ok || !data.transaction_id) throw new Error(data.error || 'failed');
            Paddle.Checkout.open({ transactionId: data.transaction_id });
            statusEl.classList.remove('show');
            upgradeBtn.disabled = false;
        } catch (e) {
            statusEl.textContent = 'Error - try again';
            statusEl.className = 'status show err';
            upgradeBtn.disabled = false;
        }
    });
}

const manageBillingBtn = document.getElementById('manage-billing-btn');
if (manageBillingBtn) {
    manageBillingBtn.addEventListener('click', async function () {
        const statusEl = document.getElementById('upgrade-status');
        manageBillingBtn.disabled = true;
        statusEl.textContent = 'Redirecting to Paddle…';
        statusEl.className = 'status show ok';
        try {
            const res = await fetch(API_BASE + '/billing/create-portal-session', { method: 'POST' });
            const data = await res.json();
            if (!res.ok || !data.url) throw new Error(data.error || 'failed');
            window.location.href = data.url;
        } catch (e) {
            statusEl.textContent = 'Error - try again';
            statusEl.className = 'status show err';
            manageBillingBtn.disabled = false;
        }
    });
}
"""

LOG_HISTORY_JS = """
function escapeHtml(s) {
    return (s || '').replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
}

function timeAgo(isoString) {
    const then = new Date(isoString);
    const diffSec = Math.floor((Date.now() - then.getTime()) / 1000);
    if (diffSec < 60) return 'just now';
    if (diffSec < 3600) return Math.floor(diffSec / 60) + 'm ago';
    if (diffSec < 86400) return Math.floor(diffSec / 3600) + 'h ago';
    return Math.floor(diffSec / 86400) + 'd ago';
}

let lhDebounce = null;
async function loadLogHistory() {
    const search = document.getElementById('lh-search').value.trim();
    const category = document.getElementById('lh-category').value;
    const params = new URLSearchParams();
    if (search) params.set('search', search);
    if (category) params.set('category', category);

    const container = document.getElementById('lh-results');
    try {
        const res = await fetch(API_BASE + '/log-history?' + params.toString());
        if (!res.ok) throw new Error('failed');
        const data = await res.json();
        if (!data.results.length) {
            container.innerHTML = '<p class="group-hint">No matching events yet.</p>';
            return;
        }
        container.innerHTML = data.results.map(function (r) {
            const color = '#' + (r.color || 0).toString(16).padStart(6, '0');
            return '<div class="group" style="border-left:3px solid ' + color + ';">' +
                '<div class="action-row" style="padding-top:0;">' +
                    '<strong style="font-size:14px;">' + escapeHtml(r.title || '(untitled)') + '</strong>' +
                    '<span class="status show ok" style="opacity:1;">' + timeAgo(r.timestamp) + '</span>' +
                '</div>' +
                (r.description ? '<p class="group-hint" style="margin-top:-4px;">' + escapeHtml(r.description) + '</p>' : '') +
                '<p class="group-hint" style="margin:0;">' + escapeHtml(r.category_label) + '</p>' +
            '</div>';
        }).join('');
    } catch (e) {
        container.innerHTML = '<p class="group-hint">Couldn\\'t load log history.</p>';
    }
}

document.getElementById('lh-search').addEventListener('input', function () {
    clearTimeout(lhDebounce);
    lhDebounce = setTimeout(loadLogHistory, 300);
});
document.getElementById('lh-category').addEventListener('change', loadLogHistory);
loadLogHistory();
"""

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

document.getElementById('save-webhook-btn').addEventListener('click', async function () {
    const statusEl = document.getElementById('webhook-status');
    try {
        const res = await fetch(API_BASE + '/security/anti-webhook', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ punishment: document.getElementById('webhook-punishment').value }),
        });
        flashStatus(statusEl, res.ok);
    } catch (e) { flashStatus(statusEl, false); }
});

document.getElementById('save-raid-btn').addEventListener('click', async function () {
    const statusEl = document.getElementById('raid-status');
    try {
        const res = await fetch(API_BASE + '/security/raid-mode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                join_threshold: parseInt(document.getElementById('raid-threshold').value, 10),
                window_seconds: parseInt(document.getElementById('raid-window').value, 10),
                action: document.getElementById('raid-action').value,
                lockdown_duration_minutes: parseInt(document.getElementById('raid-duration').value, 10),
                min_account_age_hours: parseInt(document.getElementById('raid-min-age').value, 10),
            }),
        });
        flashStatus(statusEl, res.ok);
    } catch (e) { flashStatus(statusEl, false); }
});
"""

LEVELING_JS = """
function flashStatus2(el, ok) {
    el.textContent = ok ? 'Saved' : 'Error';
    el.className = 'status show ' + (ok ? 'ok' : 'err');
    setTimeout(function () { el.classList.remove('show'); }, 1500);
}

document.getElementById('leveling-toggle').addEventListener('change', async function () {
    const statusEl = document.getElementById('leveling-toggle-status');
    try {
        const res = await fetch(API_BASE + '/leveling/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: this.checked }),
        });
        flashStatus2(statusEl, res.ok);
    } catch (e) { flashStatus2(statusEl, false); }
});

document.getElementById('save-leveling-config-btn').addEventListener('click', async function () {
    const statusEl = document.getElementById('leveling-config-status');
    try {
        const res = await fetch(API_BASE + '/leveling/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                xp_min: parseInt(document.getElementById('xp-min').value, 10),
                xp_max: parseInt(document.getElementById('xp-max').value, 10),
                cooldown_seconds: parseInt(document.getElementById('xp-cooldown').value, 10),
                announce_channel_id: document.getElementById('announce-channel').value || null,
                announce_message: document.getElementById('announce-message').value,
            }),
        });
        flashStatus2(statusEl, res.ok);
    } catch (e) { flashStatus2(statusEl, false); }
});

document.getElementById('add-ignored-channel-btn').addEventListener('click', async function () {
    const channelId = document.getElementById('add-ignored-channel-select').value;
    const statusEl = document.getElementById('ignored-channel-status');
    if (!channelId) { flashStatus2(statusEl, false); return; }
    try {
        const res = await fetch(API_BASE + '/leveling/ignored-channel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channel_id: channelId }),
        });
        if (!res.ok) throw new Error('failed');
        location.reload();
    } catch (e) { flashStatus2(statusEl, false); }
});

document.querySelectorAll('[data-remove-ignored-channel]').forEach(function (btn) {
    btn.addEventListener('click', async function () {
        const channelId = btn.getAttribute('data-remove-ignored-channel');
        try {
            const res = await fetch(API_BASE + '/leveling/ignored-channel', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel_id: channelId, remove: true }),
            });
            if (!res.ok) throw new Error('failed');
            location.reload();
        } catch (e) { btn.textContent = 'Error'; }
    });
});

document.getElementById('add-level-role-btn').addEventListener('click', async function () {
    const level = document.getElementById('add-level-role-level').value;
    const roleId = document.getElementById('add-level-role-select').value;
    const statusEl = document.getElementById('level-role-status');
    if (!level || !roleId) { flashStatus2(statusEl, false); return; }
    try {
        const res = await fetch(API_BASE + '/leveling/level-role', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ level: level, role_id: roleId }),
        });
        if (!res.ok) throw new Error('failed');
        location.reload();
    } catch (e) { flashStatus2(statusEl, false); }
});

document.querySelectorAll('[data-remove-level-role]').forEach(function (btn) {
    btn.addEventListener('click', async function () {
        const level = btn.getAttribute('data-remove-level-role');
        try {
            const res = await fetch(API_BASE + '/leveling/level-role', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ level: level, remove: true }),
            });
            if (!res.ok) throw new Error('failed');
            location.reload();
        } catch (e) { btn.textContent = 'Error'; }
    });
});
"""

VOICE_ROOMS_JS = """
function flashStatus3(el, ok) {
    el.textContent = ok ? 'Saved' : 'Error';
    el.className = 'status show ' + (ok ? 'ok' : 'err');
    setTimeout(function () { el.classList.remove('show'); }, 1500);
}

document.getElementById('vr-toggle').addEventListener('change', async function () {
    const statusEl = document.getElementById('vr-toggle-status');
    try {
        const res = await fetch(API_BASE + '/voice-rooms/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: this.checked }),
        });
        flashStatus3(statusEl, res.ok);
    } catch (e) { flashStatus3(statusEl, false); }
});

document.getElementById('save-vr-btn').addEventListener('click', async function () {
    const statusEl = document.getElementById('vr-status');
    try {
        const res = await fetch(API_BASE + '/voice-rooms/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                hub_channel_id: document.getElementById('vr-hub-channel').value || null,
                category_id: document.getElementById('vr-category').value || null,
                name_template: document.getElementById('vr-name-template').value,
                default_user_limit: parseInt(document.getElementById('vr-default-limit').value, 10),
            }),
        });
        flashStatus3(statusEl, res.ok);
    } catch (e) { flashStatus3(statusEl, false); }
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

const dashBrandSaveBtn = document.getElementById('dash-brand-save');
dashBrandSaveBtn.addEventListener('click', async function () {
    const statusEl = document.getElementById('dash-brand-status');
    const payload = {
        product_name: document.getElementById('dash-brand-name').value.trim() || null,
        logo_url: document.getElementById('dash-brand-logo').value.trim() || null,
        accent_hex: document.getElementById('dash-brand-accent').value,
    };
    dashBrandSaveBtn.disabled = true;
    try {
        const res = await fetch(API_BASE + '/dashboard-branding', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error('save failed');
        statusEl.textContent = 'Saved - reloading…';
        statusEl.className = 'status show ok';
        setTimeout(function () { location.reload(); }, 500);
    } catch (e) {
        statusEl.textContent = 'Error';
        statusEl.className = 'status show err';
        dashBrandSaveBtn.disabled = false;
    }
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
