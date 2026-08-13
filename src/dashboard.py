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
    branding_page,
    callback,
    dashboard_home,
    divider_page,
    embeds_page,
    guild_settings_page,
    login,
    logout,
    logs_page,
    overview_page,
    templates_page,
    tickets_page,
)
from dashboard_api import (
    add_divider_channel_route,
    embeds_delete_one,
    embeds_get_one,
    embeds_list,
    embeds_save,
    embeds_send,
    post_ticket_panel,
    remove_divider_channel_route,
    save_branding,
    save_divider_enabled,
    save_divider_image,
    save_guild_setting,
    save_log_color,
    save_template_slot,
)


def setup_dashboard_routes(app: web.Application, bot):
    app.router.add_get("/login", login)
    app.router.add_get("/callback", callback)
    app.router.add_get("/logout", logout)
    app.router.add_get("/dashboard", lambda request: dashboard_home(request, bot))

    app.router.add_get("/dashboard/{guild_id}", lambda request: overview_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/settings", lambda request: guild_settings_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/logs", lambda request: logs_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/branding", lambda request: branding_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/tickets", lambda request: tickets_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/embeds", lambda request: embeds_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/templates", lambda request: templates_page(request, bot))
    app.router.add_get("/dashboard/{guild_id}/divider", lambda request: divider_page(request, bot))

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
    app.router.add_post("/dashboard/{guild_id}/api/divider/enabled", lambda request: save_divider_enabled(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/divider/image", lambda request: save_divider_image(request, bot))
    app.router.add_post("/dashboard/{guild_id}/api/divider/channels", lambda request: add_divider_channel_route(request, bot))
    app.router.add_delete("/dashboard/{guild_id}/api/divider/channels/{channel_id}", lambda request: remove_divider_channel_route(request, bot))
