"""
Message Templates - links customizable, admin-facing bot messages (welcome, ticket
panel, new-ticket) to a saved Embed Builder draft, with placeholder substitution.

This is the bridge between the two embed systems:
- embed_helper.build_embed()  -> branding (color/icon/footer), always applied as a base.
- embed_builder                -> fully custom drafts an admin designs.
- THIS FILE                    -> lets an admin say "use draft X for the welcome message"
                                   instead of the hardcoded default, via a dashboard page
                                   (Templates). If no draft is assigned to a slot, the
                                   caller's own default embed is used unchanged - nothing
                                   breaks for servers that never touch this.

Each slot declares which placeholders it fills in, so the dashboard can show a "the
values you can use here" hint instead of the admin having to guess.
"""

import copy

from utils.db import get_embed_draft, get_guild_settings
from utils.embed_builder import EmbedValidationError, to_discord_embed

# (settings_key, human label, available placeholders) - settings_key is where the
# chosen draft's *name* is stored on the guild's settings document.
TEMPLATE_SLOTS = [
    (
        "welcome_embed_template",
        "Welcome Message",
        ["{member}", "{member_mention}", "{guild}", "{member_count}"],
    ),
    (
        "ticket_panel_embed_template",
        "Ticket Panel",
        ["{guild}"],
    ),
    (
        "ticket_open_embed_template",
        "New Ticket Opened",
        ["{member}", "{member_mention}", "{guild}", "{ticket_number}", "{support_role_mention}"],
    ),
]
TEMPLATE_SLOT_KEYS = {key for key, _, _ in TEMPLATE_SLOTS}

_TEXT_KEYS = ("title", "description", "footer_text", "author_name")


def _substitute_text(text: str, placeholders: dict) -> str:
    if not text:
        return text
    for token, value in placeholders.items():
        text = text.replace(token, str(value))
    return text


def _substitute(data: dict, placeholders: dict) -> dict:
    """Returns a NEW embed_json dict with every {placeholder} token replaced -
    never mutates the stored draft, since the same draft can be reused elsewhere."""
    data = copy.deepcopy(data)
    for key in _TEXT_KEYS:
        if data.get(key):
            data[key] = _substitute_text(data[key], placeholders)
    for field in data.get("fields", []):
        field["name"] = _substitute_text(field.get("name", ""), placeholders)
        field["value"] = _substitute_text(field.get("value", ""), placeholders)
    return data


async def resolve_embed(guild_id: int, slot_key: str, placeholders: dict, fallback_builder):
    """
    Returns a ready-to-send discord.Embed for a customizable message slot.
    - If the server assigned a draft to this slot (and it still exists and is valid),
      that draft is used, with placeholders substituted in.
    - Otherwise, `fallback_builder` (an async no-arg callable returning a discord.Embed)
      is used - this is always the previous hardcoded default, so nothing regresses for
      servers that never open the Templates page.
    """
    settings = await get_guild_settings(guild_id)
    draft_name = settings.get(slot_key)

    if draft_name:
        draft = await get_embed_draft(guild_id, draft_name)
        if draft:
            substituted = _substitute(draft["embed_json"], placeholders)
            try:
                return to_discord_embed(substituted)
            except EmbedValidationError:
                pass  # the draft was edited into an invalid state - fall through safely

    return await fallback_builder()
