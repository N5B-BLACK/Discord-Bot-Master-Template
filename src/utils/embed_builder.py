"""
Universal Embed Builder engine.

This is deliberately separate from utils/embed_helper.py:
- embed_helper.build_embed() = "branding" - applies the server's default color/icon/
  footer to embeds the BOT generates internally (tickets, logs, warnings, etc).
- embed_builder (this file) = a fully custom, one-off embed a server admin designs
  themselves (title, description, color, author, thumbnail, image, footer, up to 25
  fields, timestamp) - used by the dashboard's Embed Builder page and the /embed
  slash command, for announcements / rules / info panels / anything else.

Both ultimately produce a discord.Embed, but this one has NO defaults injected -
what the admin sets is exactly what gets sent. Everything round-trips through a
plain JSON-safe dict so it can be stored in MongoDB and rendered identically in the
dashboard's live preview (JS) and in the real embed (Python) - the LIMITS dict below
is the single source of truth used by both the Python validator and the dashboard JS.
"""

import discord

# Discord's real, hard API limits for embeds - keep this in sync with the
# character counters in the dashboard's embed builder page (DASHBOARD_JS).
LIMITS = {
    "title": 256,
    "description": 4096,
    "field_name": 256,
    "field_value": 1024,
    "footer_text": 2048,
    "author_name": 256,
    "max_fields": 25,
    "total": 6000,  # sum of title+description+fields+footer+author across the whole embed
}


class EmbedValidationError(Exception):
    pass


def blank_embed_json() -> dict:
    """The shape every embed draft is stored/edited as. Every key is optional except
    color, which defaults to Discord's blurple so a brand-new draft isn't colorless."""
    return {
        "title": "",
        "description": "",
        "url": "",
        "color": 0x5865F2,
        "author_name": "",
        "author_icon_url": "",
        "author_url": "",
        "thumbnail_url": "",
        "image_url": "",
        "footer_text": "",
        "footer_icon_url": "",
        "timestamp": False,
        "fields": [],  # list of {"name": str, "value": str, "inline": bool}
    }


def total_length(data: dict) -> int:
    total = len(data.get("title") or "") + len(data.get("description") or "")
    total += len(data.get("footer_text") or "") + len(data.get("author_name") or "")
    for f in data.get("fields", []):
        total += len(f.get("name") or "") + len(f.get("value") or "")
    return total


def validate(data: dict) -> None:
    """Raises EmbedValidationError with a human-readable message on the first
    limit that's broken. Called before every send/save so bad data never reaches
    Discord's API (which would just reject it with a much less useful error)."""
    if len(data.get("title") or "") > LIMITS["title"]:
        raise EmbedValidationError(f"Title is over {LIMITS['title']} characters.")
    if len(data.get("description") or "") > LIMITS["description"]:
        raise EmbedValidationError(f"Description is over {LIMITS['description']} characters.")
    if len(data.get("footer_text") or "") > LIMITS["footer_text"]:
        raise EmbedValidationError(f"Footer text is over {LIMITS['footer_text']} characters.")
    if len(data.get("author_name") or "") > LIMITS["author_name"]:
        raise EmbedValidationError(f"Author name is over {LIMITS['author_name']} characters.")

    fields = data.get("fields", [])
    if len(fields) > LIMITS["max_fields"]:
        raise EmbedValidationError(f"Too many fields (max {LIMITS['max_fields']}).")
    for i, f in enumerate(fields, start=1):
        if not (f.get("name") or "").strip():
            raise EmbedValidationError(f"Field #{i} needs a name.")
        if len(f.get("name") or "") > LIMITS["field_name"]:
            raise EmbedValidationError(f"Field #{i} name is over {LIMITS['field_name']} characters.")
        if len(f.get("value") or "") > LIMITS["field_value"]:
            raise EmbedValidationError(f"Field #{i} value is over {LIMITS['field_value']} characters.")

    if total_length(data) > LIMITS["total"]:
        raise EmbedValidationError(f"Embed is over Discord's total {LIMITS['total']}-character limit.")

    has_any_content = any(
        [
            (data.get("title") or "").strip(),
            (data.get("description") or "").strip(),
            fields,
            (data.get("author_name") or "").strip(),
            (data.get("image_url") or "").strip(),
        ]
    )
    if not has_any_content:
        raise EmbedValidationError("The embed needs at least a title, description, a field, or an image.")


def to_discord_embed(data: dict) -> discord.Embed:
    """Converts a stored embed draft (dict) into a real discord.Embed, ready to send."""
    validate(data)

    embed = discord.Embed(
        title=(data.get("title") or None),
        description=(data.get("description") or None),
        url=(data.get("url") or None),
        color=data.get("color") if data.get("color") is not None else 0x5865F2,
    )

    if data.get("author_name"):
        embed.set_author(
            name=data["author_name"],
            url=(data.get("author_url") or None),
            icon_url=(data.get("author_icon_url") or None),
        )
    if data.get("thumbnail_url"):
        embed.set_thumbnail(url=data["thumbnail_url"])
    if data.get("image_url"):
        embed.set_image(url=data["image_url"])
    if data.get("footer_text"):
        embed.set_footer(text=data["footer_text"], icon_url=(data.get("footer_icon_url") or None))
    if data.get("timestamp"):
        import datetime

        embed.timestamp = datetime.datetime.utcnow()

    for f in data.get("fields", []):
        embed.add_field(name=f["name"], value=f["value"], inline=bool(f.get("inline", True)))

    return embed
