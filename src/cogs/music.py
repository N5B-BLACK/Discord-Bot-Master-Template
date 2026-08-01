"""
/music command group + the interactive control surfaces around it:

- MusicControlView: a persistent button panel (Pause/Resume, Skip, Stop, Shuffle,
  Loop, Volume -/+, Queue) attached to the "Now Playing" message. The SAME message is
  edited in place every time a new track starts, instead of sending a new one - this
  is what every major music bot does; without it the text channel fills with spam
  within minutes. "Persistent" means the view is registered once at cog load with a
  fixed set of custom_ids, so the buttons keep responding even after a bot restart -
  interaction.guild_id (always present) is used to look up the right GuildPlayer, no
  state needs to be encoded in the button itself.
- Search picker: /music play with a plain search phrase (not a direct URL/Spotify
  link) shows the top 5 results as a Select menu instead of silently queueing
  whatever ranks first - also standard behavior for this category of bot.
- QueueView: adds Prev/Next pagination once the queue is longer than one page.

Permission model unchanged from before: anyone sharing the bot's voice channel can
control playback - no separate DJ-role system, consistent with the rest of the
template.
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils.embed_helper import build_embed
from utils.music_player import LoopMode, get_player
from utils.music_source import ExtractionError, resolve_track, search_candidates

QUEUE_PAGE_SIZE = 10


def _format_duration(seconds) -> str:
    if seconds is None:
        return "LIVE"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _same_channel_or_none(interaction: discord.Interaction, player) -> bool:
    if not player.voice_client or not player.voice_client.is_connected():
        return True
    state = interaction.user.voice
    return bool(state and state.channel and state.channel.id == player.voice_client.channel.id)


async def _now_playing_embed(guild_id: int, player) -> discord.Embed:
    track = player.current
    title = "🎶 Now Playing" + (" (via SoundCloud)" if track.source == "soundcloud" else "")
    embed = await build_embed(
        guild_id,
        title=title,
        description=f"[{track.title}]({track.webpage_url})" if track.webpage_url else track.title,
        use_brand_thumbnail=False,
    )
    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)
    embed.add_field(name="Duration", value=_format_duration(track.duration), inline=True)
    if track.uploader:
        embed.add_field(name="Uploader", value=track.uploader, inline=True)
    embed.add_field(name="Requested by", value=f"<@{track.requested_by}>", inline=True)
    loop_label = {"off": "Off", "track": "Track 🔂", "queue": "Queue 🔁"}[player.loop_mode.value]
    embed.set_footer(text=f"Volume {int(player.volume * 100)}%  ·  Loop: {loop_label}  ·  {len(player.queue)} queued")
    return embed


# -----------------------------------------------------------------
# Persistent control panel attached to every "Now Playing" message
# -----------------------------------------------------------------
class MusicControlView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)  # persistent - never expires
        self.bot = bot

    def _player(self, interaction: discord.Interaction):
        return get_player(interaction.guild_id, self.bot)

    async def _refresh_panel(self, interaction: discord.Interaction, player):
        """Re-renders the now-playing message in place after a control changes state."""
        if not player.current:
            if player.now_playing_message:
                try:
                    await player.now_playing_message.edit(content="⏹️ Playback stopped.", embed=None, view=None)
                except discord.HTTPException:
                    pass
                player.now_playing_message = None
            return
        embed = await _now_playing_embed(interaction.guild_id, player)
        if player.now_playing_message:
            try:
                await player.now_playing_message.edit(embed=embed, view=self)
                return
            except discord.HTTPException:
                pass
        player.now_playing_message = await interaction.channel.send(embed=embed, view=self)

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.secondary, custom_id="music:pauseresume")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self._player(interaction)
        if not await self._guard(interaction, player):
            return
        if player.voice_client and player.voice_client.is_playing():
            player.pause()
            await interaction.response.send_message("⏸️ Paused.", ephemeral=True)
        elif player.voice_client and player.voice_client.is_paused():
            player.resume()
            await interaction.response.send_message("▶️ Resumed.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ Nothing to pause/resume.", ephemeral=True)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="music:skip")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self._player(interaction)
        if not await self._guard(interaction, player):
            return
        skipped = player.current.title if player.current else "the track"
        if player.skip():
            await interaction.response.send_message(f"⏭️ Skipped **{skipped}**.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ Nothing is playing.", ephemeral=True)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="music:stop")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self._player(interaction)
        if not await self._guard(interaction, player):
            return
        await player.disconnect()
        await interaction.response.send_message("⏹️ Stopped and left the voice channel.", ephemeral=True)
        try:
            await interaction.message.edit(content="⏹️ Playback stopped.", embed=None, view=None)
        except discord.HTTPException:
            pass

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary, custom_id="music:shuffle")
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self._player(interaction)
        if not await self._guard(interaction, player):
            return
        if not player.queue:
            await interaction.response.send_message("⚠️ The queue is empty.", ephemeral=True)
            return
        player.shuffle()
        await interaction.response.send_message("🔀 Queue shuffled.", ephemeral=True)

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary, custom_id="music:loop")
    async def loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self._player(interaction)
        if not await self._guard(interaction, player):
            return
        order = [LoopMode.OFF, LoopMode.TRACK, LoopMode.QUEUE]
        player.loop_mode = order[(order.index(player.loop_mode) + 1) % len(order)]
        await interaction.response.defer()
        await self._refresh_panel(interaction, player)

    @discord.ui.button(emoji="🔉", style=discord.ButtonStyle.secondary, custom_id="music:vol_down")
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self._player(interaction)
        if not await self._guard(interaction, player):
            return
        player.set_volume(player.volume - 0.1)
        await interaction.response.defer()
        await self._refresh_panel(interaction, player)

    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, custom_id="music:vol_up")
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self._player(interaction)
        if not await self._guard(interaction, player):
            return
        player.set_volume(player.volume + 0.1)
        await interaction.response.defer()
        await self._refresh_panel(interaction, player)

    @discord.ui.button(emoji="📜", label="Queue", style=discord.ButtonStyle.primary, custom_id="music:queue")
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self._player(interaction)
        embed, view = build_queue_page(interaction.guild_id, player, page=0)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _guard(self, interaction: discord.Interaction, player) -> bool:
        if _same_channel_or_none(interaction, player):
            return True
        await interaction.response.send_message(
            f"⚠️ You need to be in {player.voice_client.channel.mention} to do that.", ephemeral=True
        )
        return False


# -----------------------------------------------------------------
# Paginated queue view
# -----------------------------------------------------------------
def build_queue_page(guild_id: int, player, page: int):
    tracks = list(player.queue)
    total_pages = max(1, (len(tracks) + QUEUE_PAGE_SIZE - 1) // QUEUE_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = tracks[page * QUEUE_PAGE_SIZE : (page + 1) * QUEUE_PAGE_SIZE]

    embed = discord.Embed(title="🎵 Queue", color=discord.Color.blurple())
    if player.current:
        embed.add_field(
            name="Now playing",
            value=f"[{player.current.title}]({player.current.webpage_url}) · {_format_duration(player.current.duration)}",
            inline=False,
        )
    if not tracks:
        embed.description = "Nothing else queued."
    else:
        start = page * QUEUE_PAGE_SIZE
        lines = [
            f"**{start + i + 1}.** [{t.title}]({t.webpage_url}) · {_format_duration(t.duration)}"
            for i, t in enumerate(chunk)
        ]
        embed.add_field(name=f"Up next ({len(tracks)} total)", value="\n".join(lines), inline=False)
        embed.set_footer(text=f"Page {page + 1}/{total_pages}")

    view = QueueView(guild_id, page, total_pages) if total_pages > 1 else None
    return embed, view


class QueueView(discord.ui.View):
    def __init__(self, guild_id: int, page: int, total_pages: int):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.page = page
        self.total_pages = total_pages
        self.prev_btn.disabled = page <= 0
        self.next_btn.disabled = page >= total_pages - 1

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from utils.music_player import players

        player = players.get(self.guild_id)
        embed, view = build_queue_page(self.guild_id, player, self.page - 1)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from utils.music_player import players

        player = players.get(self.guild_id)
        embed, view = build_queue_page(self.guild_id, player, self.page + 1)
        await interaction.response.edit_message(embed=embed, view=view)


# -----------------------------------------------------------------
# Search-result picker (top 5), shown for plain-text queries
# -----------------------------------------------------------------
class SearchPickView(discord.ui.View):
    def __init__(self, candidates: list[dict], requester_id: int, on_pick):
        super().__init__(timeout=30)
        self.requester_id = requester_id
        self.on_pick = on_pick
        options = [
            discord.SelectOption(
                label=c["title"][:100],
                description=f"{c['uploader'] or 'Unknown'} · {_format_duration(c['duration'])}"[:100],
                value=str(i),
            )
            for i, c in enumerate(candidates)
        ]
        self.candidates = candidates
        self.select.options = options

    @discord.ui.select(placeholder="Pick the result to queue...")
    async def select(self, interaction: discord.Interaction, select: discord.ui.Select):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("⚠️ Only the person who searched can pick a result.", ephemeral=True)
            return
        choice = self.candidates[int(select.values[0])]
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=f"✅ Selected **{choice['title']}** - queueing...", view=self)
        await self.on_pick(interaction, choice["url"], choice["title"])

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class MusicGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="music", description="Play and control music")


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.group = MusicGroup()
        self.control_view = MusicControlView(bot)
        bot.add_view(self.control_view)  # registers it as persistent (survives restarts)
        self._register_commands()
        bot.tree.add_command(self.group)

    @staticmethod
    def _user_voice_channel(interaction: discord.Interaction):
        state = interaction.user.voice
        return state.channel if state else None

    async def _require_same_channel(self, interaction: discord.Interaction, player) -> bool:
        if _same_channel_or_none(interaction, player):
            return True
        await interaction.response.send_message(
            f"⚠️ You need to be in {player.voice_client.channel.mention} to do that.", ephemeral=True
        )
        return False

    async def _start_or_queue(self, interaction: discord.Interaction, player, track, announce_channel):
        player.text_channel = announce_channel
        position = player.enqueue(track)

        if player.is_playing:
            embed = await build_embed(
                interaction.guild_id,
                title="➕ Added to queue",
                description=f"[{track.title}]({track.webpage_url})" if track.webpage_url else track.title,
                use_brand_thumbnail=False,
            )
            if track.thumbnail:
                embed.set_thumbnail(url=track.thumbnail)
            embed.add_field(name="Position in queue", value=str(position), inline=True)
            embed.add_field(name="Duration", value=_format_duration(track.duration), inline=True)
            await announce_channel.send(embed=embed)
        else:
            await player.play_next()

    async def _on_track_start(self, guild_id: int, track):
        player = get_player(guild_id, self.bot)
        if not player.text_channel:
            return
        embed = await _now_playing_embed(guild_id, player)
        if player.now_playing_message:
            try:
                await player.now_playing_message.edit(embed=embed, view=self.control_view)
                return
            except discord.HTTPException:
                pass
        try:
            player.now_playing_message = await player.text_channel.send(embed=embed, view=self.control_view)
        except discord.HTTPException:
            pass

    def _register_commands(self):
        group = self.group
        bot = self.bot

        @group.command(name="play", description="Play a song (search, URL, or Spotify link) - joins your voice channel if needed")
        @app_commands.describe(query="A song name, YouTube/SoundCloud URL, or Spotify link")
        async def play(interaction: discord.Interaction, query: str):
            user_channel = self._user_voice_channel(interaction)
            if not user_channel:
                await interaction.response.send_message("⚠️ Join a voice channel first.", ephemeral=True)
                return

            player = get_player(interaction.guild_id, bot)
            if not await self._require_same_channel(interaction, player):
                return

            await interaction.response.defer()

            try:
                await player.connect(user_channel)
            except discord.ClientException as e:
                await interaction.followup.send(f"⚠️ Couldn't join your voice channel: {e}")
                return

            try:
                candidates = await search_candidates(query, limit=5)
            except ExtractionError as e:
                await interaction.followup.send(f"⚠️ {e}")
                return

            if not candidates:
                # direct URL / Spotify link - resolve straight away, no picker needed
                try:
                    track = await resolve_track(query, interaction.user.id)
                except ExtractionError as e:
                    await interaction.followup.send(f"⚠️ {e}")
                    return
                player.on_track_start = lambda t: self._on_track_start(interaction.guild_id, t)
                await interaction.followup.send(f"🔎 Found **{track.title}**.")
                await self._start_or_queue(interaction, player, track, interaction.channel)
                return

            # plain search query - show a pick-list of the top results
            async def on_pick(pick_interaction: discord.Interaction, chosen_url: str, chosen_title: str):
                try:
                    track = await resolve_track(chosen_url, interaction.user.id, fallback_query=chosen_title)
                except ExtractionError as e:
                    await interaction.followup.send(f"⚠️ {e}")
                    return
                player.on_track_start = lambda t: self._on_track_start(interaction.guild_id, t)
                await self._start_or_queue(interaction, player, track, interaction.channel)

            view = SearchPickView(candidates, interaction.user.id, on_pick)
            lines = "\n".join(
                f"**{i + 1}.** {c['title']} · {_format_duration(c['duration'])}" for i, c in enumerate(candidates)
            )
            await interaction.followup.send(f"🔎 Results for **{query}**:\n{lines}", view=view)

        @group.command(name="skip", description="Skip the current song")
        async def skip(interaction: discord.Interaction):
            player = get_player(interaction.guild_id, bot)
            if not await self._require_same_channel(interaction, player):
                return
            skipped = player.current
            if player.skip():
                await interaction.response.send_message(f"⏭️ Skipped **{skipped.title if skipped else 'the track'}**.")
            else:
                await interaction.response.send_message("⚠️ Nothing is playing.", ephemeral=True)

        @group.command(name="pause", description="Pause playback")
        async def pause(interaction: discord.Interaction):
            player = get_player(interaction.guild_id, bot)
            if not await self._require_same_channel(interaction, player):
                return
            if player.pause():
                await interaction.response.send_message("⏸️ Paused.")
            else:
                await interaction.response.send_message("⚠️ Nothing is playing.", ephemeral=True)

        @group.command(name="resume", description="Resume playback")
        async def resume(interaction: discord.Interaction):
            player = get_player(interaction.guild_id, bot)
            if not await self._require_same_channel(interaction, player):
                return
            if player.resume():
                await interaction.response.send_message("▶️ Resumed.")
            else:
                await interaction.response.send_message("⚠️ Nothing is paused.", ephemeral=True)

        @group.command(name="stop", description="Stop playback, clear the queue, and leave the voice channel")
        async def stop(interaction: discord.Interaction):
            player = get_player(interaction.guild_id, bot)
            if not await self._require_same_channel(interaction, player):
                return
            if player.now_playing_message:
                try:
                    await player.now_playing_message.edit(content="⏹️ Playback stopped.", embed=None, view=None)
                except discord.HTTPException:
                    pass
            await player.disconnect()
            await interaction.response.send_message("⏹️ Stopped and left the voice channel.")

        @group.command(name="leave", description="Leave the voice channel (alias of /music stop)")
        async def leave(interaction: discord.Interaction):
            player = get_player(interaction.guild_id, bot)
            if not await self._require_same_channel(interaction, player):
                return
            await player.disconnect()
            await interaction.response.send_message("👋 Left the voice channel.")

        @group.command(name="queue", description="Show the upcoming queue")
        async def show_queue(interaction: discord.Interaction):
            player = get_player(interaction.guild_id, bot)
            embed, view = build_queue_page(interaction.guild_id, player, page=0)
            await interaction.response.send_message(embed=embed, view=view)

        @group.command(name="nowplaying", description="Show the currently playing song")
        async def now_playing(interaction: discord.Interaction):
            player = get_player(interaction.guild_id, bot)
            if not player.current:
                await interaction.response.send_message("⚠️ Nothing is playing.", ephemeral=True)
                return
            embed = await _now_playing_embed(interaction.guild_id, player)
            await interaction.response.send_message(embed=embed)

        @group.command(name="volume", description="Set playback volume (0-200%)")
        @app_commands.describe(percent="Volume percentage, 0-200 (100 = normal)")
        async def volume(interaction: discord.Interaction, percent: app_commands.Range[int, 0, 200]):
            player = get_player(interaction.guild_id, bot)
            if not await self._require_same_channel(interaction, player):
                return
            player.set_volume(percent / 100)
            await interaction.response.send_message(f"🔊 Volume set to {percent}%.")

        @group.command(name="loop", description="Set loop mode")
        @app_commands.choices(mode=[
            app_commands.Choice(name="Off", value="off"),
            app_commands.Choice(name="Current track", value="track"),
            app_commands.Choice(name="Whole queue", value="queue"),
        ])
        async def loop(interaction: discord.Interaction, mode: app_commands.Choice[str]):
            player = get_player(interaction.guild_id, bot)
            if not await self._require_same_channel(interaction, player):
                return
            player.loop_mode = LoopMode(mode.value)
            await interaction.response.send_message(f"🔁 Loop mode set to **{mode.name}**.")

        @group.command(name="shuffle", description="Shuffle the upcoming queue")
        async def shuffle(interaction: discord.Interaction):
            player = get_player(interaction.guild_id, bot)
            if not await self._require_same_channel(interaction, player):
                return
            if not player.queue:
                await interaction.response.send_message("⚠️ The queue is empty.", ephemeral=True)
                return
            player.shuffle()
            await interaction.response.send_message("🔀 Queue shuffled.")

        @group.command(name="remove", description="Remove a specific song from the queue")
        @app_commands.describe(position="The position shown in /music queue (1 = next up)")
        async def remove(interaction: discord.Interaction, position: int):
            player = get_player(interaction.guild_id, bot)
            if not await self._require_same_channel(interaction, player):
                return
            track = player.remove(position)
            if track:
                await interaction.response.send_message(f"🗑️ Removed **{track.title}** from the queue.")
            else:
                await interaction.response.send_message("⚠️ Invalid queue position.", ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return
        player = get_player(member.guild.id, self.bot)
        if not player.voice_client or not player.voice_client.is_connected():
            return
        channel = player.voice_client.channel
        real_members = [m for m in channel.members if not m.bot]
        if not real_members:
            await player.disconnect()


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
