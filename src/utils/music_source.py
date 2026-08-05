"""
Music source extraction - the ONLY file that talks to yt-dlp directly. Isolated
deliberately: if this deployment ever needs to swap to a different backend (e.g. a
Lavalink node instead of direct yt-dlp downloads), only this file changes;
cogs/music.py and utils/music_player.py only ever see plain Track objects.

yt-dlp is synchronous/blocking (it does real network I/O + subprocess work), so every
call here runs in a thread executor - never call yt-dlp directly on the event loop,
it would freeze the whole bot (every guild, not just the one playing music) for the
duration of the extraction.

RELIABILITY NOTE: YouTube increasingly returns "Sign in to confirm you're not a bot"
for requests from datacenter/cloud IPs (Render included) - a bot-detection challenge,
not the same as a hard IP ban, but with the same practical effect: the track won't
play. Two mitigations are built in:
1. Optional cookies (YTDLP_COOKIES_B64 env var, base64-encoded Netscape cookies.txt
   exported from a real logged-in YouTube session) - the most reliable fix, but has a
   real account-ban-risk tradeoff the deployer should weigh themselves. Entirely
   optional; everything works without it, just less reliably on flagged IPs.
2. Automatic SoundCloud fallback - if a YouTube resolution hits this specific
   bot-check error and a plain-text query is available (from the search picker), it's
   silently retried on SoundCloud, which doesn't have this restriction. Smaller
   catalog than YouTube, but a real, working result beats an error.
"""

import asyncio
import base64
import dataclasses
import functools
import re
import tempfile

import aiohttp
import yt_dlp

import config

SPOTIFY_URL_RE = re.compile(r"open\.spotify\.com/(track|episode)/([A-Za-z0-9]+)")
BOT_CHECK_PATTERN = re.compile(r"sign in to confirm|not a bot", re.IGNORECASE)

# Optional cookies support - see module docstring. Written to a temp file once at
# import time (not per-request); Render's filesystem is otherwise ephemeral but stays
# put for the life of the running process, which is all a temp file needs here.
_COOKIES_FILE_PATH = None
if getattr(config, "YTDLP_COOKIES_B64", None):
    try:
        _cookies_data = base64.b64decode(config.YTDLP_COOKIES_B64)
        _tmp = tempfile.NamedTemporaryFile(delete=False, suffix="_cookies.txt")
        _tmp.write(_cookies_data)
        _tmp.close()
        _COOKIES_FILE_PATH = _tmp.name
    except Exception:
        _COOKIES_FILE_PATH = None  # bad/missing env var - silently continue without cookies

YTDLP_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch1",
    "source_address": "0.0.0.0",  # avoids picking an unroutable IPv6 address in some containers
    "extract_flat": False,
}
if _COOKIES_FILE_PATH:
    YTDLP_OPTS["cookiefile"] = _COOKIES_FILE_PATH

# Same shape as YTDLP_OPTS but searches SoundCloud instead - the automatic fallback
# when YouTube hits its bot-check wall. No cookies needed/supported here.
YTDLP_OPTS_SOUNDCLOUD = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "scsearch1",
    "source_address": "0.0.0.0",
    "extract_flat": False,
}

@dataclasses.dataclass
class Track:
    title: str
    stream_url: str
    webpage_url: str
    duration: int | None  # seconds, None for live streams
    thumbnail: str | None
    uploader: str | None
    requested_by: int  # user ID - stored as an ID, not a mention, so it survives serialization if ever needed
    source: str = "youtube"  # "youtube" or "soundcloud" - lets the UI note when a fallback was used
    http_headers: dict | None = None  # some CDNs (SoundCloud especially) need the same headers yt-dlp
    # used to authorize the URL, or they serve an empty/broken response - attached to the
    # urllib request in utils/http_audio_source.py, not passed to FFmpeg (which no longer touches the network at all)


class ExtractionError(Exception):
    """Raised when a track can't be found or streamed - always has a user-safe message."""


def _is_bot_check_error(exc: Exception) -> bool:
    return bool(BOT_CHECK_PATTERN.search(str(exc)))


def _is_url(query: str) -> bool:
    return query.strip().lower().startswith(("http://", "https://"))


async def _spotify_title(url: str) -> str | None:
    """Spotify's public oEmbed endpoint needs no API key/auth and returns the track
    title - just enough to build a good search query. Returns None on any failure
    (network issue, deleted track, etc) so the caller can fall back gracefully."""
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


def _extract_sync(query: str, opts: dict) -> dict:
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(query, download=False)
    if "entries" in info:  # search results come back as a playlist-shaped dict
        entries = [e for e in info["entries"] if e]
        if not entries:
            raise ExtractionError(f"No results found for **{query}**.")
        info = entries[0]
    return info


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

    (This lightweight metadata-only listing rarely triggers YouTube's bot-check -
    that wall shows up specifically when resolving the actual playable stream, i.e.
    in resolve_track() below.)
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


def _track_from_info(info: dict, query: str, requested_by: int, source: str) -> Track:
    stream_url = info.get("url")
    http_headers = info.get("http_headers")
    if not stream_url:
        # some extractors return a "formats" list instead of a top-level url
        formats = info.get("formats") or []
        audio_formats = [f for f in formats if f.get("acodec") != "none" and f.get("url")]
        if not audio_formats:
            raise ExtractionError(f"Found **{info.get('title', query)}** but couldn't get a playable audio stream.")
        chosen = audio_formats[-1]
        stream_url = chosen["url"]
        http_headers = chosen.get("http_headers") or http_headers

    return Track(
        title=info.get("title") or query,
        stream_url=stream_url,
        webpage_url=info.get("webpage_url") or info.get("original_url") or "",
        duration=info.get("duration"),
        thumbnail=info.get("thumbnail"),
        uploader=info.get("uploader"),
        requested_by=requested_by,
        source=source,
        http_headers=http_headers or None,
    )


async def resolve_track(query: str, requested_by: int, fallback_query: str | None = None) -> Track:
    """
    Accepts either a direct URL (YouTube, SoundCloud, etc - anything yt-dlp supports)
    or a plain search phrase, and returns a ready-to-play Track. Spotify links are
    special-cased: Spotify doesn't allow third-party audio streaming, so the track
    title is resolved via oEmbed and re-searched instead.

    fallback_query: plain text (usually the picked result's title, from the search
    picker) to retry on SoundCloud if the primary YouTube resolution hits its
    bot-check wall. Without it (e.g. a raw URL typed directly with no known title), a
    bot-check failure is surfaced as a clear error instead of silently guessing.
    """
    query = query.strip()

    if "open.spotify.com" in query:
        title = await _spotify_title(query)
        if not title:
            raise ExtractionError(
                "Couldn't read that Spotify link (it may be private or region-locked). "
                "Try searching the song name instead."
            )
        fallback_query = fallback_query or title
        query = title  # falls through to a normal search below

    loop = asyncio.get_event_loop()
    try:
        info = await loop.run_in_executor(None, functools.partial(_extract_sync, query, YTDLP_OPTS))
        return _track_from_info(info, query, requested_by, source="youtube")
    except ExtractionError:
        raise
    except Exception as e:
        if _is_bot_check_error(e):
            retry_text = fallback_query or (query if not _is_url(query) else None)
            if retry_text:
                try:
                    sc_info = await loop.run_in_executor(
                        None, functools.partial(_extract_sync, retry_text, YTDLP_OPTS_SOUNDCLOUD)
                    )
                    return _track_from_info(sc_info, retry_text, requested_by, source="soundcloud")
                except Exception:
                    pass  # fall through to the YouTube error below - it's the more actionable one
            raise ExtractionError(
                "YouTube is asking this bot to verify it's not a bot (a known issue on cloud hosts). "
                "Try again in a moment, or ask the bot owner to configure cookies for more reliable playback."
            ) from e
        raise ExtractionError(f"Couldn't play that: {str(e).split('ERROR:')[-1].strip()[:200]}") from e
