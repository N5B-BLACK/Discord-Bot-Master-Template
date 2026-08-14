"""
Rank card image generation (Phase 2 - Leveling).

Pure Pillow, no external image-generation API - draws a fixed-size card with
the member's avatar, name, level, rank, and an XP progress bar. Deliberately
simple/flat design (no drop shadows, no gradients beyond the accent bar) so it
stays legible at Discord's embed-image render size and is cheap to render
(no >50ms-per-request budget issues even on Render's free tier).

Fonts are bundled in assets/fonts/ (Poppins, OFL-licensed) rather than relying
on system fonts, since the hosting environment isn't guaranteed to have any
fonts installed at all - Pillow's fallback bitmap font is unusably small.
"""

import io
import os

import aiohttp
from PIL import Image, ImageDraw, ImageFont

_ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "fonts")

CARD_WIDTH = 900
CARD_HEIGHT = 260
AVATAR_SIZE = 180
PADDING = 40

BG_COLOR = (30, 31, 38)
BAR_BG_COLOR = (54, 55, 66)
TEXT_COLOR = (255, 255, 255)
MUTED_COLOR = (168, 170, 184)


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(os.path.join(_ASSETS_DIR, name), size)


def _circular_avatar(avatar_bytes: bytes, size: int) -> Image.Image:
    avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    circular = Image.new("RGBA", (size, size))
    circular.paste(avatar, (0, 0), mask)
    return circular


async def _fetch_avatar_bytes(avatar_url: str) -> bytes:
    async with aiohttp.ClientSession() as http:
        async with http.get(avatar_url) as resp:
            return await resp.read()


async def generate_rank_card(
    display_name: str,
    avatar_url: str,
    level: int,
    rank: int,
    xp_into_level: int,
    xp_needed: int,
    accent_color: tuple[int, int, int] = (88, 101, 242),
) -> io.BytesIO:
    """Returns a BytesIO PNG ready to attach to a discord.File."""
    card = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(card)

    # Avatar (circular, with a thin accent-colored ring)
    avatar_bytes = await _fetch_avatar_bytes(avatar_url)
    avatar_img = _circular_avatar(avatar_bytes, AVATAR_SIZE)
    ring_size = AVATAR_SIZE + 8
    ring = Image.new("RGBA", (ring_size, ring_size), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse((0, 0, ring_size, ring_size), fill=accent_color + (255,))
    card.paste(ring, (PADDING - 4, PADDING - 4), ring)
    card.paste(avatar_img, (PADDING, PADDING), avatar_img)

    text_x = PADDING + AVATAR_SIZE + 36

    # Display name (truncated if too long to avoid overlapping the rank/level block)
    name_font = _font("Poppins-SemiBold.ttf", 40)
    name = display_name if len(display_name) <= 18 else display_name[:17] + "…"
    draw.text((text_x, PADDING + 4), name, font=name_font, fill=TEXT_COLOR)

    # RANK / LEVEL, right-aligned
    stat_font = _font("Poppins-Bold.ttf", 32)
    stat_label_font = _font("Poppins-Regular.ttf", 18)
    rank_text = f"#{rank}"
    level_text = f"{level}"
    rank_w = draw.textlength(rank_text, font=stat_font)
    level_w = draw.textlength(level_text, font=stat_font)
    right_edge = CARD_WIDTH - PADDING
    draw.text((right_edge - level_w, PADDING), level_text, font=stat_font, fill=accent_color)
    draw.text((right_edge - level_w - draw.textlength("LEVEL  ", font=stat_label_font) - 10, PADDING + 10),
               "LEVEL", font=stat_label_font, fill=MUTED_COLOR)
    rank_y = PADDING + 55
    draw.text((right_edge - rank_w, rank_y), rank_text, font=stat_font, fill=TEXT_COLOR)
    draw.text((right_edge - rank_w - draw.textlength("RANK  ", font=stat_label_font) - 10, rank_y + 10),
               "RANK", font=stat_label_font, fill=MUTED_COLOR)

    # XP progress bar
    bar_x, bar_y = text_x, PADDING + 90
    bar_w, bar_h = CARD_WIDTH - text_x - PADDING, 28
    draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), radius=bar_h // 2, fill=BAR_BG_COLOR)
    progress_ratio = min(1.0, xp_into_level / xp_needed) if xp_needed else 0
    filled_w = max(bar_h, int(bar_w * progress_ratio))  # never shrink below a full circle so it doesn't look broken at 0%
    if progress_ratio > 0:
        draw.rounded_rectangle((bar_x, bar_y, bar_x + filled_w, bar_y + bar_h), radius=bar_h // 2, fill=accent_color)

    xp_font = _font("Poppins-Regular.ttf", 20)
    xp_text = f"{xp_into_level:,} / {xp_needed:,} XP"
    draw.text((bar_x, bar_y + bar_h + 10), xp_text, font=xp_font, fill=MUTED_COLOR)

    buf = io.BytesIO()
    card.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf
