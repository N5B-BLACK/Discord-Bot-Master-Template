"""
Music source extraction - the ONLY file that talks to yt-dlp directly. Isolated
deliberately: if this deployment ever needs to swap to a different backend (e.g. a
Lavalink node instead of direct yt-dlp downloads - see the Render/YouTube IP-block
note in music_player.py's module docstring), only this file changes; cogs/music.py
and utils/music_player.py only ever see plain Track objects.

yt-dlp is synchronous/blocking (it does real network I/O + subprocess work), so every
call here runs in a thread executor - never call yt-dlp directly on the event loop,
it would freeze the whole bot (every guild, not just the one playing music) for the
duration of the extraction.
"""

import asyncio
import dataclasses
import functools
import re

import aiohttp
import yt_dlp

SPOTIFY_URL_RE = re.compile(r"open\.spotify\.com/(track|episode)/([A-Za-z0-9]+)")

YTDLP_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch1",
    "source_address": "0.0.0.0",  # avoids picking an unroutable IPv6 address in some containers
    "extract_flat": False,
}

FFMPEG_BEFORE_OPTS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTS = "-vn"


@dataclasses.dataclass
class Track:
    title: str
    stream_url: str
    webpage_url: str
    duration: int | None  # seconds, None for live streams
    thumbnail: str | None
    uploader: str | None
    requested_by: int  # user ID - stored as an ID, not a mention, so it survives serialization if ever needed


class ExtractionError(Exception):
    """Raised when a track can't be found or streamed - always has a user-safe message."""


async def _spotify_title(url: str) -> str | None:
    """Spotify's public oEmbed endpoint needs no API key/auth and returns the track
    title - just enough to build a good YouTube search query. Returns None on any
    failure (network issue, deleted track, etc) so the caller can fall back gracefully."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://open.spotify.com/oembed", params={"url": url}, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("title")
    except Exception:
        return None


def _extract_sync(query: str) -> dict:
    with yt_dlp.YoutubeDL(YTDLP_OPTS) as ydl:
        info = ydl.extract_info(query, download=False)
    if "entries" in info:  # search results come back as a playlist-shaped dict
        entries = [e for e in info["entries"] if e]
        if not entries:
            raise ExtractionError(f"No results found for **{query}**.")
        info = entries[0]
    return info


def _is_url(query: str) -> bool:
    return query.strip().lower().startswith(("http://", "https://"))


def _search_sync(query: str, limit: int) -> list[dict]:
    opts = dict(YTDLP_OPTS)
    opts["extract_flat"] = True  # metadata only, no format resolution - fast, used just to list choices
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    return [e for e in (info.get("entries") or []) if e]


async def search_candidates(query: str, limit: int = 5) -> list[dict]:
    """
    Returns up to `limit` lightweight search results ({title, url, duration, uploader})
    for a plain text query, WITHOUT resolving a playable stream yet - used to show the
    user a pick-list (like every major music bot does) instead of silently grabbing
    whatever yt-dlp's default search ranks first. Direct URLs skip this entirely -
    resolve_track() handles those straight away.
    """
    if _is_url(query) or "open.spotify.com" in query:
        return []  # nothing to pick between - resolve_track() will handle it directly

    loop = asyncio.get_event_loop()
    try:
        entries = await loop.run_in_executor(None, functools.partial(_search_sync, query, limit))
    except Exception as e:
        raise ExtractionError(f"Search failed: {e}") from e

    if not entries:
        raise ExtractionError(f"No results found for **{query}**.")

    return [
        {
            "title": e.get("title") or "Unknown title",
            "url": e.get("url") or e.get("webpage_url") or e.get("id"),
            "duration": e.get("duration"),
            "uploader": e.get("uploader") or e.get("channel"),
        }
        for e in entries
    ]


async def resolve_track(query: str, requested_by: int) -> Track:
    """
    Accepts either a direct URL (YouTube, SoundCloud, etc - anything yt-dlp supports)
    or a plain search phrase, and returns a ready-to-play Track. Spotify links are
    special-cased: Spotify doesn't allow third-party audio streaming, so the track
    title is resolved via oEmbed and re-searched on YouTube instead.
    """
    query = query.strip()

    if "open.spotify.com" in query:
        title = await _spotify_title(query)
        if not title:
            raise ExtractionError(
                "Couldn't read that Spotify link (it may be private or region-locked). "
                "Try searching the song name instead."
            )
        query = title  # falls through to a normal search below

    loop = asyncio.get_event_loop()
    try:
        info = await loop.run_in_executor(None, functools.partial(_extract_sync, query))
    except yt_dlp.utils.DownloadError as e:
        raise ExtractionError(f"Couldn't play that: {str(e).split('ERROR:')[-1].strip()[:200]}") from e
    except ExtractionError:
        raise
    except Exception as e:
        raise ExtractionError(f"Unexpected error while looking that up: {e}") from e

    stream_url = info.get("url")
    if not stream_url:
        # some extractors return a "formats" list instead of a top-level url
        formats = info.get("formats") or []
        audio_formats = [f for f in formats if f.get("acodec") != "none" and f.get("url")]
        if not audio_formats:
            raise ExtractionError(f"Found **{info.get('title', query)}** but couldn't get a playable audio stream.")
        stream_url = audio_formats[-1]["url"]

    return Track(
        title=info.get("title") or query,
        stream_url=stream_url,
        webpage_url=info.get("webpage_url") or info.get("original_url") or "",
        duration=info.get("duration"),
        thumbnail=info.get("thumbnail"),
        uploader=info.get("uploader"),
        requested_by=requested_by,
    )
