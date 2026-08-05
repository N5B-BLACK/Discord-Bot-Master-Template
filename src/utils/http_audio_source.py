"""
HTTP-streaming source for FFmpeg's pipe mode.

WHY THIS FILE EXISTS: the original design let FFmpeg fetch the stream URL itself
(`discord.FFmpegPCMAudio(url, ...)`), which is the standard, simplest approach and
works for most deployments. On this project's actual hosting (Render, using
imageio-ffmpeg's bundled static ffmpeg binary), that specific combination reliably
SEGFAULTS (confirmed via direct reproduction - it crashes on ANY network URL, HTTP or
HTTPS, with or without custom headers; a local/piped input never crashes). That's a
bug in that particular static ffmpeg build's network I/O, not fixable from here.

The fix: never let FFmpeg touch the network. Python (via urllib, a completely
different, well-tested code path) fetches the audio bytes and streams them into
FFmpeg's stdin - FFmpeg only ever decodes a local pipe, which works fine. This also
sidesteps needing FFmpeg's -headers flag at all (SoundCloud's requirement is
satisfied by attaching the same headers to the urllib request instead).
"""

import io
import logging
import urllib.error
import urllib.request

logger = logging.getLogger("bot")

DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
CHUNK_SIZE = 65536
CONNECT_TIMEOUT = 15


class HTTPStreamSource(io.RawIOBase):
    """A file-like object (only .read() is needed - that's all discord.py's pipe
    writer calls) that streams an HTTP(S) URL's body via urllib. Passed to
    discord.FFmpegPCMAudio(source, pipe=True, ...) so FFmpeg reads from stdin
    instead of opening the URL itself."""

    def __init__(self, url: str, headers: dict | None = None):
        request_headers = {"User-Agent": DEFAULT_USER_AGENT}
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(url, headers=request_headers)
        # Blocking call - callers MUST construct this off the event loop (see
        # open_http_stream below), never directly inside an async function.
        self._response = urllib.request.urlopen(request, timeout=CONNECT_TIMEOUT)

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        try:
            return self._response.read(size if size and size > 0 else CHUNK_SIZE)
        except Exception as e:
            logger.debug(f"HTTPStreamSource read ended: {e}")
            return b""  # signals EOF to discord.py's pipe writer - stream ends cleanly either way

    def close(self) -> None:
        try:
            self._response.close()
        except Exception:
            pass
        super().close()


def open_http_stream_sync(url: str, headers: dict | None = None) -> HTTPStreamSource:
    """Synchronous constructor - meant to be called via loop.run_in_executor(),
    never directly on the event loop (opening the connection blocks)."""
    return HTTPStreamSource(url, headers)
