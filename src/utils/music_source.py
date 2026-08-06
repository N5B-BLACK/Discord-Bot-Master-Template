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
play. Mitigations built in:
1. Format selection explicitly EXCLUDES HLS/DASH-protocol formats (see FORMAT_SELECTOR
   below). This isn't about bot-check at all - it's a hard architectural requirement:
   this bot pipes audio bytes through FFmpeg's stdin (see http_audio_source.py) rather
   than letting FFmpeg fetch the URL itself (that segfaults on this host's FFmpeg
   build - see music_player.py's docstring). HLS/DASH streams are manifests that
   reference separate segment URLs FFmpeg must fetch itself mid-decode - fundamentally
   incompatible with the pipe approach, since nothing refetches those segments over
   the pipe. Without this exclusion, a video whose only available formats are
   HLS-based fails with a confusing "Invalid data found when processing input" that
   looks unrelated to the real cause.
2. Optional cookies (YTDLP_COOKIES_B64 env var, base64-encoded Netscape cookies.txt
   exported from a real logged-in YouTube session) - the most reliable fix for the
   bot-check specifically, but has a real account-ban-risk tradeoff the deployer
   should weigh themselves. Entirely optional; everything works without it, just less
   reliably on flagged IPs.
3. Automatic SoundCloud fallback - triggered on ANY YouTube resolution failure (not
   just pattern-matched bot-check errors - format-unavailable, region-locked, etc all
   degrade the same way) as long as a plain-text query is available to search with.
   Smaller catalog than YouTube, but a real, working result beats an error.
"""

import asyncio
import base64
import dataclasses
import functools
import logging
import re
import tempfile
import urllib.error
import urllib.request

import aiohttp
import yt_dlp

import config

logger = logging.getLogger("bot")

SPOTIFY_URL_RE = re.compile(r"open\.spotify\.com/(track|episode)/([A-Za-z0-9]+)")
BOT_CHECK_PATTERN = re.compile(r"sign in to confirm|not a bot", re.IGNORECASE)
NON_AUDIO_CONTENT_TYPES = ("text/html", "text/plain", "application/json")

# Excludes any format whose protocol contains "m3u8" (HLS) or "dash" - see the
# RELIABILITY NOTE above for why. Falls back to plain "bestaudio/best" only if
# every single available format is HLS/DASH (rare, but better to attempt playback
# than to refuse outright - the sniff check below still catches an actually-broken
# result either way).
FORMAT_SELECTOR = "bestaudio[protocol!*=m3u8][protocol!*=dash]/best[protocol!*=m3u8][protocol!*=dash]/bestaudio/best"

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
    "format": FORMAT_SELECTOR,
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
# when YouTube resolution fails for any reason. No cookies needed/supported here.
YTDLP_OPTS_SOUNDCLOUD = {
    "format": FORMAT_SELECTOR,
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
    is_hls_or_dash: bool = False  # True if this format is a manifest-based stream (m3u8/DASH) - incompatible
    # with piping through FFmpeg's stdin (see FORMAT_SELECTOR above); resolve_track() avoids returning
    # these when it can, but this flag lets a caller double-check/reject one if it ever slips through


class ExtractionError(Exception):
    """Raised when a track can't be found or streamed - always has a user-safe message."""


def _is_bot_check_error(exc: Exception) -> bool:
    return bool(BOT_CHECK_PATTERN.search(str(exc)))


def _sniff_is_audio_sync(url: str, headers: dict | None) -> bool:
    """
    Confirms the resolved stream URL actually serves audio/video, not an HTML/error
    page. Necessary because YouTube sometimes "succeeds" at extraction (no exception
    raised anywhere in yt-dlp) while the URL itself only serves a sign-in/consent page
    - or an outright 401/403 - when actually fetched. A tiny ranged request (first 2KB)
    is enough to check without downloading the whole track just to validate it.

    Fails OPEN (returns True) for ambiguous problems - timeouts, DNS issues, connection
    resets - since those shouldn't block a track that might genuinely be fine. But
    fails CLOSED (returns False) for an explicit 401/403, and for a 2xx response whose
    Content-Type is clearly not audio/video - both are strong, specific signals of
    exactly this bot-check problem, not a transient network hiccup.
    """
    try:
        request_headers = dict(headers or {})
        request_headers.setdefault("User-Agent", "Mozilla/5.0")
        request_headers["Range"] = "bytes=0-2048"
        request = urllib.request.Request(url, headers=request_headers)
        with urllib.request.urlopen(request, timeout=8) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
    except urllib.error.HTTPError as e:
        return e.code not in (401, 403)
    except Exception:
        return True
    if not content_type:
        return True
    return not any(content_type.startswith(bad) for bad in NON_AUDIO_CONTENT_TYPES)


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
    protocol = info.get("protocol") or ""
    if not stream_url:
        # some extractors return a "formats" list instead of a top-level url
        formats = info.get("formats") or []
        audio_formats = [f for f in formats if f.get("acodec") != "none" and f.get("url")]
        if not audio_formats:
            raise ExtractionError(f"Found **{info.get('title', query)}** but couldn't get a playable audio stream.")
        chosen = audio_formats[-1]
        stream_url = chosen["url"]
        http_headers = chosen.get("http_headers") or http_headers
        protocol = chosen.get("protocol") or protocol

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
        is_hls_or_dash=("m3u8" in protocol or "dash" in protocol),
    )


class _ValidationFailure(ExtractionError):
    """Raised by _resolve_and_validate when extraction succeeded but the result failed
    a post-extraction check (HLS/DASH, content sniff) - carries the partially-built
    Track so resolve_track() can reuse its title for a SoundCloud retry without paying
    for a second, redundant extraction just to re-discover the same title."""

    def __init__(self, message: str, track: "Track"):
        super().__init__(message)
        self.track = track


async def _resolve_and_validate(query: str, opts: dict, requested_by: int, source: str, loop) -> Track:
    """Extracts + builds a Track + runs it through every validity check (HLS rejection,
    content sniff). Raises on any failure - never returns a Track that's known-bad.
    Shared by both the YouTube attempt and the SoundCloud fallback attempt, so both
    get the exact same validation instead of the fallback being a lower-trust path."""
    info = await loop.run_in_executor(None, functools.partial(_extract_sync, query, opts))
    track = _track_from_info(info, query, requested_by, source=source)

    if track.is_hls_or_dash:
        raise _ValidationFailure(
            f"'{track.title}' is only available as an adaptive stream (HLS/DASH), which this bot can't play.",
            track,
        )

    is_audio = await loop.run_in_executor(None, functools.partial(_sniff_is_audio_sync, track.stream_url, track.http_headers))
    if not is_audio:
        raise _ValidationFailure(
            f"'{track.title}' didn't return playable audio (likely a bot-check or region block).", track
        )

    return track


async def resolve_track(query: str, requested_by: int, fallback_query: str | None = None) -> Track:
    """
    Accepts either a direct URL (YouTube, SoundCloud, etc - anything yt-dlp supports)
    or a plain search phrase, and returns a ready-to-play Track. Spotify links are
    special-cased: Spotify doesn't allow third-party audio streaming, so the track
    title is resolved via oEmbed and re-searched instead.

    fallback_query: plain text (usually the picked result's title, from the search
    picker) to retry on SoundCloud if the primary YouTube resolution fails for any
    reason. Without it (e.g. a raw URL with no extractable title at all), a failure is
    surfaced as a clear error instead of silently guessing what to search for.
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
        logger.info(f"[music] Resolving via YouTube: {query!r}")
        track = await _resolve_and_validate(query, YTDLP_OPTS, requested_by, "youtube", loop)
        logger.info(f"[music] YouTube resolution OK: '{track.title}' (protocol-safe, passed content sniff)")
        return track
    except Exception as e:
        logger.warning(f"[music] YouTube resolution failed for {query!r}: {e}")

        yt_title_hint = e.track.title if isinstance(e, _ValidationFailure) else None
        if yt_title_hint is None and fallback_query is None:
            # Extraction itself raised with nothing usable at all (no partial Track) -
            # one lightweight, metadata-only attempt (skips format/stream resolution,
            # so it's far less likely to hit the same wall) just to get a search-able
            # title instead of giving up immediately.
            try:
                info = await loop.run_in_executor(
                    None, functools.partial(_extract_sync, query, {**YTDLP_OPTS, "extract_flat": True})
                )
                yt_title_hint = info.get("title")
            except Exception:
                pass

        retry_text = fallback_query or yt_title_hint or (query if not _is_url(query) else None)
        if not retry_text:
            raise ExtractionError(
                "Couldn't play that from YouTube, and there's no title to search on SoundCloud instead "
                "(try searching by song name rather than pasting the link)."
            ) from e

        try:
            logger.info(f"[music] Falling back to SoundCloud: {retry_text!r}")
            track = await _resolve_and_validate(retry_text, YTDLP_OPTS_SOUNDCLOUD, requested_by, "soundcloud", loop)
            logger.info(f"[music] SoundCloud fallback OK: '{track.title}'")
            return track
        except Exception as e2:
            logger.warning(f"[music] SoundCloud fallback also failed for {retry_text!r}: {e2}")
            if _is_bot_check_error(e):
                raise ExtractionError(
                    "YouTube is asking this bot to verify it's not a bot (a known issue on cloud hosts), "
                    "and the SoundCloud fallback didn't find a match either. "
                    "Try again in a moment, or ask the bot owner to configure cookies for more reliable playback."
                ) from e2
            raise ExtractionError(
                f"Couldn't play that: {str(e).split('ERROR:')[-1].strip()[:200]}"
            ) from e2
