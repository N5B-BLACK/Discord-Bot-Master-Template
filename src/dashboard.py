"""
Dashboard - route wiring only (Phase 0 refactor).

dashboard.py used to be one 2,078-line file (session/auth/CSS + every page +
every API route + this wiring). It's now split three ways so future modules
(security suite, leveling, etc. - see utils/module_registry.py) each get their
own file instead of growing this one indefinitely:

- dashboard_core.py  - session handling, shared CSS/shell, the auth guard
- dashboard_pages.py - every HTML page route (OAuth + guild-scoped pages)
- dashboard_api.py   - every JSON API route ("Save" button targets)

This file just imports the handlers from those two and registers them on the
aiohttp app - main.py's `from dashboard import setup_dashboard_routes` is
unchanged, so no other file needed to change.
"""

from aiohttp import web

from dashboard_pages import (
    admin_page,
    branding_page,
    callback,
    dashboard_home,
    divider_page,
    embeds_page,
    guild_settings_page,
    leveling_page,
    log_history_page,
    login,
    login_start,
    logout,
    logs_page,
    overview_page,
    privacy_page,
    refund_page,
    security_page,
    templates_page,
    terms_page,
    tickets_page,
    upgrade_page,
    voice_rooms_page,
)
from dashboard_api import (
    add_divider_channel_route,
    embeds_delete_one,
    embeds_get_one,
    embeds_list,
    embeds_save,
    embeds_send,
    get_log_history,
    post_ticket_panel,
    remove_divider_channel_route,
    save_anti_nuke_config,
    save_anti_spam_config,
    save_anti_webhook_config,
    save_banned_word,
    save_branding,
    save_dashboard_branding,
    save_divider_enabled,
    save_divider_image,
    save_guild_setting,
    save_level_role,
    save_leveling_config,
    save_leveling_ignored_channel,
    save_leveling_toggle,
    save_link_whitelist_channel,
    save_link_whitelist_domain,
    save_log_color,
    save_raid_mode_config,
    save_security_log_channel,
    save_security_toggle,
    save_security_whitelist_user,
    save_template_slot,
    save_voice_rooms_config,
    save_voice_rooms_toggle,
    create_checkout_session_route,
    create_portal_session_route,
    paddle_webhook,
)


def setup_dashboard_routes(app: web.Application, bot):
    app.router.add_get("/", login)
    app.router.add_get("/login", login)
    app.router.add_get("/terms", terms_page)
    app.router.add_get("/privacy", privacy_page)
    app.router.add_get("/refund", refund_page)
    app.router.add_get("/login/start", login_start)
    app.router.add_get("/callback", callback)
    app.router.add_get("/logout", logout)
    app.router.add_get("/dashboard", lambda request: dashboard_home(request, bot))
    app.router.add_get("/admin", lambda request: admin_page(request, bot))

    app.router.add_get("/dashboard/{guild_id}", lambda request: overview_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/settings", lambda request: guild_settings_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/upgrade", lambda request: upgrade_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/security", lambda request: security_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/leveling", lambda request: leveling_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/voice-rooms", lambda request: voice_rooms_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/logs", lambda request: logs_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/log-history", lambda request: log_history_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/api/log-history", lambda request: get_log_history(request, bot))
    app.router.add_get("/dashboard/{guild_id}/branding", lambda request: branding_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/tickets", lambda request: tickets_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/embeds", lambda request: embeds_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/templates", lambda request: templates_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/divider", lambda request: divider_page(request, bot))

    app.router.add_post("/dashboard/{guild_id}/api/settings", lambda request: save_guild_setting(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/log-color", lambda request: save_log_color(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/branding", lambda request: save_branding(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/dashboard-branding", lambda request: save_dashboard_branding(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/templates", lambda request: save_template_slot(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/post-ticket-panel", lambda request: post_ticket_panel(request, bot))
    app.router.add_get("/dashboard/{guild_id}/api/embeds/list", lambda request: embeds_list(request, bot))
    app.router.add_get("/dashboard/{guild_id}/api/embeds/{name}", lambda request: embeds_get_one(request, bot))
    app.router.add_delete("/dashboard/{guild_id}/api/embeds/{name}", lambda request: embeds_delete_one(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/embeds/save", lambda request: embeds_save(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/embeds/send", lambda request: embeds_send(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/divider/enabled", lambda request: save_divider_enabled(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/divider/image", lambda request: save_divider_image(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/divider/channels", lambda request: add_divider_channel_route(request, bot))
    app.router.add_delete("/dashboard/{guild_id}/api/divider/channels/{channel_id}", lambda request: remove_divider_channel_route(request, bot))

    app.router.add_post("/dashboard/{guild_id}/api/security/toggle", lambda request: save_security_toggle(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/security/log-channel", lambda request: save_security_log_channel(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/security/anti-nuke", lambda request: save_anti_nuke_config(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/security/anti-spam", lambda request: save_anti_spam_config(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/security/whitelist-user", lambda request: save_security_whitelist_user(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/security/banned-word", lambda request: save_banned_word(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/security/link-domain", lambda request: save_link_whitelist_domain(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/security/link-channel", lambda request: save_link_whitelist_channel(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/security/anti-webhook", lambda request: save_anti_webhook_config(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/security/raid-mode", lambda request: save_raid_mode_config(request, bot))

    app.router.add_post("/dashboard/{guild_id}/api/leveling/toggle", lambda request: save_leveling_toggle(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/leveling/config", lambda request: save_leveling_config(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/leveling/ignored-channel", lambda request: save_leveling_ignored_channel(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/leveling/level-role", lambda request: save_level_role(request, bot))

    app.router.add_post("/dashboard/{guild_id}/api/voice-rooms/toggle", lambda request: save_voice_rooms_toggle(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/voice-rooms/config", lambda request: save_voice_rooms_config(request, bot))

    app.router.add_post("/dashboard/{guild_id}/api/billing/create-checkout-session", lambda request: create_checkout_session_route(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/billing/create-portal-session", lambda request: create_portal_session_route(request, bot))
    # Not guild-scoped - Paddle calls this directly, authenticated by webhook signature instead of a session cookie.
    app.router.add_post("/webhooks/paddle", paddle_webhook)
