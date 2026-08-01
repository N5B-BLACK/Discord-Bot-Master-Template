"""
Per-guild music playback engine.

IMPORTANT DEPLOYMENT NOTE (flagged before this feature was built, confirmed still
true): this streams audio by having yt-dlp resolve a direct googlevideo.com URL and
handing it to FFmpeg. Cloud hosts with shared/rotating IPs - Render's free tier
included - are commonly rate-limited or blocked by YouTube for this kind of traffic.
This is an infrastructure limitation, not a bug in this code. If /play starts failing
consistently in production but works locally, that's almost certainly what's
happening - the fix is a dedicated IP, a proxy, or migrating to a Lavalink node, not a
code change here. utils/music_source.py is kept isolated specifically so that swap is
possible later without touching this file or the commands in cogs/music.py.

Architecture: one GuildPlayer per guild that's ever played music, kept in a registry
dict (`players`) keyed by guild ID. Every guild's playback is fully independent - the
bot can be playing a different queue in every server it's in at the same time,
because each one gets its own discord.VoiceClient (Discord's own per-guild voice
connections) and its own GuildPlayer instance/queue here. There's no shared "one
song at a time" state anywhere in this file.

Idle handling: a guild's voice connection auto-disconnects after IDLE_DISCONNECT_SECONDS
of nothing playing (empty queue) OR immediately if the voice channel becomes empty of
real (non-bot) members - both keep the bot from sitting connected to dead channels.
"""

import asyncio
import logging
import random
from collections import deque
from enum import Enum

import discord
import imageio_ffmpeg

from utils.music_source import Track

logger = logging.getLogger("bot")

IDLE_DISCONNECT_SECONDS = 180
FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
FFMPEG_BEFORE_OPTS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTS = "-vn"


class LoopMode(Enum):
    OFF = "off"
    TRACK = "track"
    QUEUE = "queue"


class GuildPlayer:
    """Owns one guild's voice connection, queue, and playback state."""

    def __init__(self, guild_id: int, bot: discord.Client):
        self.guild_id = guild_id
        self.bot = bot
        self.voice_client: discord.VoiceClient | None = None
        self.queue: deque[Track] = deque()
        self.current: Track | None = None
        self.loop_mode: LoopMode = LoopMode.OFF
        self.volume: float = 0.5  # 0.0 - 2.0, FFmpegPCMAudio/PCMVolumeTransformer scale
        self.text_channel: discord.abc.Messageable | None = None  # where to post "Now Playing"
        self.now_playing_message: discord.Message | None = None  # edited in place, not resent every track
        self.on_track_start = None  # optional async callback(track) set by the cog, for now-playing messages
        self._idle_task: asyncio.Task | None = None
        self._skip_requested = False
        self._lock = asyncio.Lock()

    # -----------------------------------------------------------------
    # Connection
    # -----------------------------------------------------------------
    async def connect(self, channel: discord.VoiceChannel) -> None:
        if self.voice_client and self.voice_client.is_connected():
            if self.voice_client.channel.id != channel.id:
                await self.voice_client.move_to(channel)
            return
        self.voice_client = await channel.connect()

    async def disconnect(self) -> None:
        self._cancel_idle_timer()
        if self.voice_client:
            try:
                await self.voice_client.disconnect(force=True)
            except Exception:
                pass
            self.voice_client = None
        self.queue.clear()
        self.current = None
        self.now_playing_message = None

    # -----------------------------------------------------------------
    # Queue control
    # -----------------------------------------------------------------
    def enqueue(self, track: Track) -> int:
        """Adds a track to the end of the queue, returns its 1-based position."""
        self.queue.append(track)
        return len(self.queue)

    def shuffle(self) -> None:
        random.shuffle(self.queue)

    def remove(self, position: int) -> Track | None:
        """1-based position, matching what /queue displays to users."""
        if 1 <= position <= len(self.queue):
            q = list(self.queue)
            track = q.pop(position - 1)
            self.queue = deque(q)
            return track
        return None

    def clear_queue(self) -> None:
        self.queue.clear()

    # -----------------------------------------------------------------
    # Playback
    # -----------------------------------------------------------------
    async def play_next(self) -> None:
        """Advances to the next track and starts playback. Safe to call even if
        something is already playing (it'll be stopped first)."""
        async with self._lock:
            self._cancel_idle_timer()

            if self.loop_mode == LoopMode.TRACK and self.current and not self._skip_requested:
                next_track = self.current
            elif self.queue:
                next_track = self.queue.popleft()
                if self.loop_mode == LoopMode.QUEUE and self.current:
                    self.queue.append(self.current)
            else:
                next_track = None

            self._skip_requested = False
            self.current = next_track

            if next_track is None:
                self._start_idle_timer()
                return

            if not self.voice_client or not self.voice_client.is_connected():
                return

            source = discord.FFmpegPCMAudio(
                next_track.stream_url,
                executable=FFMPEG_EXE,
                before_options=FFMPEG_BEFORE_OPTS,
                options=FFMPEG_OPTS,
            )
            transformed = discord.PCMVolumeTransformer(source, volume=self.volume)

            def _after(error):
                if error:
                    logger.error(f"Playback error in guild {self.guild_id}: {error}")
                # after() runs in a different thread - hop back onto the bot's event loop
                fut = asyncio.run_coroutine_threadsafe(self.play_next(), self.bot.loop)
                try:
                    fut.result()
                except Exception as e:
                    logger.error(f"Error advancing queue in guild {self.guild_id}: {e}")

            self.voice_client.play(transformed, after=_after)

            if self.on_track_start:
                try:
                    await self.on_track_start(next_track)
                except Exception as e:
                    logger.error(f"on_track_start callback failed in guild {self.guild_id}: {e}")

    def skip(self) -> bool:
        """Stops the current track (triggers the after-callback -> play_next()).
        Returns False if nothing was playing."""
        if not self.voice_client or not self.voice_client.is_playing():
            return False
        self._skip_requested = True
        self.voice_client.stop()
        return True

    def pause(self) -> bool:
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.pause()
            return True
        return False

    def resume(self) -> bool:
        if self.voice_client and self.voice_client.is_paused():
            self.voice_client.resume()
            return True
        return False

    def set_volume(self, value: float) -> None:
        self.volume = max(0.0, min(2.0, value))
        if self.voice_client and isinstance(self.voice_client.source, discord.PCMVolumeTransformer):
            self.voice_client.source.volume = self.volume

    @property
    def is_playing(self) -> bool:
        return bool(self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()))

    # -----------------------------------------------------------------
    # Idle auto-disconnect
    # -----------------------------------------------------------------
    def _start_idle_timer(self) -> None:
        self._cancel_idle_timer()
        self._idle_task = self.bot.loop.create_task(self._idle_disconnect())

    def _cancel_idle_timer(self) -> None:
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
        self._idle_task = None

    async def _idle_disconnect(self) -> None:
        try:
            await asyncio.sleep(IDLE_DISCONNECT_SECONDS)
            await self.disconnect()
            if self.text_channel:
                try:
                    await self.text_channel.send("👋 Left the voice channel - nothing queued for a while.")
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass


# Registry - one GuildPlayer per guild, created lazily on first use.
players: dict[int, GuildPlayer] = {}


def get_player(guild_id: int, bot: discord.Client) -> GuildPlayer:
    if guild_id not in players:
        players[guild_id] = GuildPlayer(guild_id, bot)
    return players[guild_id]
