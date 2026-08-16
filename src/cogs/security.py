"""
Security Suite (Phase 1 of the roadmap) - anti-nuke, anti-spam, anti-link, word filter.

Each sub-system is independently toggleable per server (utils/db.py's `security`
settings block) and off by default - a server only gets punished/moderated once an
admin explicitly turns a sub-system on via /security.

Detection is in-memory (per-guild sliding-window trackers), not database-backed:
these need to react in milliseconds during an actual attack, and losing the
window's history on a bot restart is an acceptable tradeoff for that speed - a
mid-attack restart is rare, and the attack itself keeps re-triggering fresh events
that rebuild the window in seconds anyway.

Punishment is deliberately conservative by default (strip_roles, not ban) since a
false positive (e.g. a legit admin doing genuine cleanup) is far more damaging if
it's an unrecoverable ban. Anyone in `security.whitelist_user_ids`, the server
owner, and the bot itself are always exempt from anti-nuke punishment.
"""

import asyncio
import datetime
import re
import time
from collections import defaultdict, deque
import discord
from discord import app_commands
from discord.ext import commands

from utils.db import (
    add_banned_word,
    add_link_whitelist_channel,
    add_link_whitelist_domain,
    add_security_whitelist_user,
    get_guild_settings,
    remove_banned_word,
    remove_link_whitelist_channel,
    remove_link_whitelist_domain,
    remove_security_whitelist_user,
    record_event_log,
    set_security_setting,
)
from utils.embed_helper import build_embed
from utils.licensing import is_module_available
from utils.log_helper import get_recent_audit_entry, send_guild_log

URL_PATTERN = re.compile(r"https?://([^\s/]+)", re.IGNORECASE)

# Destructive audit-log actions anti-nuke watches. Each maps to a human label
# used in the alert embed.
NUKE_ACTIONS = {
    discord.AuditLogAction.channel_delete: "Channel Deleted",
    discord.AuditLogAction.role_delete: "Role Deleted",
}


async def _send_security_log(guild: discord.Guild, settings: dict, embed: discord.Embed) -> None:
    """Security alerts go to security.log_channel_id if set, else fall back to the
    warn log so an alert is never silently dropped just because an admin only set
    up one of the two channels. Recorded to Log History exactly once either way:
    the fallback path already records via send_guild_log() itself, so only the
    dedicated-channel path (which bypasses that helper) needs to record here."""
    security = settings.get("security", {})
    channel_id = security.get("log_channel_id")
    channel = guild.get_channel(channel_id) if channel_id else None

    if channel is None:
        await send_guild_log(guild, "warn_log_channel_id", embed)
        return

    description = embed.description or ""
    if not description and embed.fields:
        description = " · ".join(f"{f.name}: {f.value}" for f in embed.fields if f.name and f.value)
    await record_event_log(
        guild.id, "security_log_channel_id", embed.title or "", description,
        embed.colour.value if embed.colour else 0,
    )
    await channel.send(embed=embed)


class Security(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # guild_id -> user_id -> deque[timestamp] of recent destructive actions
        self._nuke_events: dict[int, dict[int, deque]] = defaultdict(lambda: defaultdict(deque))
        # guild_id -> user_id -> deque[timestamp] of recent messages
        self._spam_events: dict[int, dict[int, deque]] = defaultdict(lambda: defaultdict(deque))
        # guild_id -> deque[timestamp] of recent member joins
        self._join_events: dict[int, deque] = defaultdict(deque)
        # guild_id -> raid state while a lockdown/kick response is active
        self._raid_state: dict[int, dict] = {}
        # guild_id -> discord.VerificationLevel saved before a lockdown, to restore after
        self._pre_raid_verification: dict[int, discord.VerificationLevel] = {}

    security_group = app_commands.Group(
        name="security", description="Configure the security suite (anti-nuke / anti-spam / anti-link / word filter)"
    )

    # -----------------------------------------------------------------
    # Shared punishment helper (anti-nuke + anti-webhook use the same two options)
    # -----------------------------------------------------------------
    async def _punish_actor(self, guild: discord.Guild, actor: discord.abc.User, punishment: str, reason: str) -> str:
        member = guild.get_member(actor.id)
        if member is None:
            return "none (member left)"
        try:
            if punishment == "ban":
                await guild.ban(member, reason=reason)
                return "banned"
            roles_to_remove = [r for r in member.roles if r != guild.default_role]
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason=reason)
            return "all roles stripped"
        except discord.Forbidden:
            return "none (missing permissions)"

    def _is_exempt(self, guild: discord.Guild, settings: dict, actor_id: int) -> bool:
        if actor_id == guild.owner_id or actor_id == self.bot.user.id:
            return True
        return actor_id in settings.get("security", {}).get("whitelist_user_ids", [])

    # -----------------------------------------------------------------
    # Anti-nuke
    # -----------------------------------------------------------------
    async def _handle_nuke_event(self, guild: discord.Guild, action: discord.AuditLogAction, target_id: int, label: str):
        settings = await get_guild_settings(guild.id)
        conf = settings.get("security", {}).get("anti_nuke", {})
        if not conf.get("enabled") or not is_module_available(settings, "anti_nuke"):
            return

        entry = await get_recent_audit_entry(guild, action, target_id)
        if entry is None or entry.user is None:
            return
        actor = entry.user

        if self._is_exempt(guild, settings, actor.id):
            return

        now = time.time()
        window = conf.get("window_seconds", 10)
        threshold = conf.get("action_threshold", 3)
        events = self._nuke_events[guild.id][actor.id]
        events.append(now)
        while events and now - events[0] > window:
            events.popleft()

        if len(events) < threshold:
            return
        events.clear()  # reset so we don't re-punish every single subsequent event

        action_taken = await self._punish_actor(
            guild, actor, conf.get("punishment", "strip_roles"), "Anti-nuke: destructive action threshold exceeded"
        )

        embed = await build_embed(
            guild.id,
            title="🚨 Anti-Nuke Triggered",
            color=discord.Color.red(),
            use_brand_thumbnail=False,
        )
        embed.timestamp = datetime.datetime.utcnow()
        embed.add_field(name="User", value=f"{actor} ({actor.id})", inline=False)
        embed.add_field(name="Trigger", value=f"{threshold}+ destructive actions in {window}s (last: {label})", inline=False)
        embed.add_field(name="Action taken", value=action_taken, inline=False)
        await _send_security_log(guild, settings, embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        await self._handle_nuke_event(channel.guild, discord.AuditLogAction.channel_delete, channel.id, "Channel Deleted")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        await self._handle_nuke_event(role.guild, discord.AuditLogAction.role_delete, role.id, "Role Deleted")

    # -----------------------------------------------------------------
    # Anti-webhook - an unauthorized webhook is a common raid/phishing vector
    # (lets an attacker post as a fake "official" message even after being kicked).
    # Deletes the webhook itself in addition to punishing whoever created it.
    # -----------------------------------------------------------------
    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        settings = await get_guild_settings(guild.id)
        conf = settings.get("security", {}).get("anti_webhook", {})
        if not conf.get("enabled") or not is_module_available(settings, "anti_webhook"):
            return

        # webhook_create's audit-log target is the webhook itself (unknown ahead of
        # time), so unlike other events here we scan recent audit-log entries by
        # action + recency rather than by a specific target_id.
        await asyncio.sleep(1.5)
        entry = None
        try:
            async for e in guild.audit_logs(limit=5, action=discord.AuditLogAction.webhook_create):
                if (discord.utils.utcnow() - e.created_at).total_seconds() < 10:
                    entry = e
                    break
        except discord.Forbidden:
            return
        if entry is None or entry.user is None:
            return
        actor = entry.user

        if self._is_exempt(guild, settings, actor.id):
            return

        try:
            for webhook in await channel.webhooks():
                if webhook.user and webhook.user.id == actor.id:
                    await webhook.delete(reason="Anti-webhook: unauthorized webhook creation")
        except discord.Forbidden:
            pass

        action_taken = await self._punish_actor(
            guild, actor, conf.get("punishment", "strip_roles"), "Anti-webhook: unauthorized webhook creation"
        )

        embed = await build_embed(
            guild.id, title="🪝 Anti-Webhook Triggered", color=discord.Color.red(), use_brand_thumbnail=False
        )
        embed.timestamp = datetime.datetime.utcnow()
        embed.add_field(name="User", value=f"{actor} ({actor.id})", inline=False)
        embed.add_field(name="Channel", value=channel.mention, inline=False)
        embed.add_field(name="Action taken", value=action_taken, inline=False)
        await _send_security_log(guild, settings, embed)

    # -----------------------------------------------------------------
    # Raid mode - detects a burst of joins and responds with either a temporary
    # verification-level lockdown, or kicking new-enough accounts that join
    # during the burst window (attackers' throwaway accounts are almost always
    # brand new; legitimate members joining together, e.g. after a public
    # announcement, are usually a mix of account ages).
    # -----------------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        settings = await get_guild_settings(guild.id)
        conf = settings.get("security", {}).get("raid_mode", {})
        if not conf.get("enabled") or not is_module_available(settings, "raid_mode"):
            return

        now = time.time()
        window = conf.get("window_seconds", 10)
        threshold = conf.get("join_threshold", 5)
        events = self._join_events[guild.id]
        events.append(now)
        while events and now - events[0] > window:
            events.popleft()

        raid_active = self._raid_state.get(guild.id, {}).get("active_until", 0) > now
        action = conf.get("action", "lockdown")

        if not raid_active and len(events) >= threshold:
            await self._trigger_raid_response(guild, settings, conf)
            raid_active = True

        # While a raid response is active, keep applying kick_new_accounts to
        # every subsequent joiner too - not just the ones that tripped the trigger.
        if raid_active and action == "kick_new_accounts":
            await self._maybe_kick_new_account(member, conf)

    async def _trigger_raid_response(self, guild: discord.Guild, settings: dict, conf: dict) -> None:
        action = conf.get("action", "lockdown")
        duration_minutes = conf.get("lockdown_duration_minutes", 15)
        self._raid_state[guild.id] = {"active_until": time.time() + duration_minutes * 60}

        action_taken = "alerted only"
        if action == "lockdown":
            try:
                self._pre_raid_verification[guild.id] = guild.verification_level
                await guild.edit(
                    verification_level=discord.VerificationLevel.highest,
                    reason="Raid mode: join burst detected",
                )
                action_taken = f"verification level raised to Highest for {duration_minutes} minutes"
                self.bot.loop.call_later(
                    duration_minutes * 60,
                    lambda: self.bot.loop.create_task(self._revert_lockdown(guild.id)),
                )
            except discord.Forbidden:
                action_taken = "failed (missing Manage Server permission)"
        elif action == "kick_new_accounts":
            action_taken = f"kicking accounts younger than {conf.get('min_account_age_hours', 24)}h for {duration_minutes} minutes"

        embed = await build_embed(
            guild.id, title="🚨 Raid Mode Triggered", color=discord.Color.red(), use_brand_thumbnail=False
        )
        embed.timestamp = datetime.datetime.utcnow()
        embed.add_field(
            name="Trigger", value=f"{conf.get('join_threshold', 5)}+ joins in {conf.get('window_seconds', 10)}s", inline=False
        )
        embed.add_field(name="Response", value=action_taken, inline=False)
        await _send_security_log(guild, settings, embed)

    async def _revert_lockdown(self, guild_id: int) -> None:
        guild = self.bot.get_guild(guild_id)
        original = self._pre_raid_verification.pop(guild_id, None)
        if guild is None or original is None:
            return
        try:
            await guild.edit(verification_level=original, reason="Raid mode: lockdown expired")
        except discord.Forbidden:
            pass

    async def _maybe_kick_new_account(self, member: discord.Member, conf: dict) -> None:
        min_age_hours = conf.get("min_account_age_hours", 24)
        age = discord.utils.utcnow() - member.created_at
        if age.total_seconds() >= min_age_hours * 3600:
            return
        try:
            await member.kick(reason=f"Raid mode: account younger than {min_age_hours}h during join burst")
        except discord.Forbidden:
            pass

    # -----------------------------------------------------------------
    # Message-based systems: anti-spam, anti-link, word filter
    # (checked in that order per message; each can independently delete/act)
    # -----------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        if isinstance(message.author, discord.Member) and message.author.guild_permissions.administrator:
            return  # admins are exempt from all message-based security checks

        settings = await get_guild_settings(message.guild.id)
        security = settings.get("security", {})

        if await self._check_word_filter(message, settings, security):
            return
        if await self._check_anti_link(message, settings, security):
            return
        await self._check_anti_spam(message, settings, security)

    async def _check_word_filter(self, message: discord.Message, settings: dict, security: dict) -> bool:
        conf = security.get("word_filter", {})
        if not conf.get("enabled") or not is_module_available(settings, "word_filter"):
            return False
        banned = conf.get("banned_words", [])
        if not banned:
            return False
        content_lower = message.content.lower()
        hit = next((w for w in banned if re.search(rf"\b{re.escape(w)}\b", content_lower)), None)
        if hit is None:
            return False

        try:
            await message.delete()
        except discord.Forbidden:
            pass

        embed = await build_embed(
            message.guild.id, title="🧹 Word Filter", color=discord.Color.orange(), use_brand_thumbnail=False
        )
        embed.timestamp = datetime.datetime.utcnow()
        embed.add_field(name="Member", value=f"{message.author} ({message.author.id})", inline=False)
        embed.add_field(name="Channel", value=message.channel.mention, inline=False)
        embed.add_field(name="Matched word", value=hit, inline=False)
        await _send_security_log(message.guild, settings, embed)
        return True

    async def _check_anti_link(self, message: discord.Message, settings: dict, security: dict) -> bool:
        conf = security.get("anti_link", {})
        if not conf.get("enabled") or not is_module_available(settings, "anti_link"):
            return False
        if message.channel.id in conf.get("whitelist_channel_ids", []):
            return False
        match = URL_PATTERN.search(message.content)
        if match is None:
            return False
        domain = match.group(1).lower()
        if any(domain == d or domain.endswith(f".{d}") for d in conf.get("whitelist_domains", [])):
            return False

        try:
            await message.delete()
        except discord.Forbidden:
            pass

        embed = await build_embed(
            message.guild.id, title="🔗 Anti-Link", color=discord.Color.orange(), use_brand_thumbnail=False
        )
        embed.timestamp = datetime.datetime.utcnow()
        embed.add_field(name="Member", value=f"{message.author} ({message.author.id})", inline=False)
        embed.add_field(name="Channel", value=message.channel.mention, inline=False)
        embed.add_field(name="Domain", value=domain, inline=False)
        await _send_security_log(message.guild, settings, embed)
        return True

    async def _check_anti_spam(self, message: discord.Message, settings: dict, security: dict) -> None:
        conf = security.get("anti_spam", {})
        if not conf.get("enabled") or not is_module_available(settings, "anti_spam"):
            return

        now = time.time()
        window = conf.get("window_seconds", 7)
        threshold = conf.get("message_threshold", 6)
        events = self._spam_events[message.guild.id][message.author.id]
        events.append(now)
        while events and now - events[0] > window:
            events.popleft()

        if len(events) < threshold:
            return
        events.clear()

        timeout_seconds = conf.get("timeout_seconds", 300)
        action_taken = "none (missing permissions)"
        try:
            if isinstance(message.author, discord.Member):
                until = discord.utils.utcnow() + datetime.timedelta(seconds=timeout_seconds)
                await message.author.timeout(until, reason="Anti-spam: message rate threshold exceeded")
                action_taken = f"timed out for {timeout_seconds}s"
        except discord.Forbidden:
            pass

        embed = await build_embed(
            message.guild.id, title="⏱️ Anti-Spam Triggered", color=discord.Color.orange(), use_brand_thumbnail=False
        )
        embed.timestamp = datetime.datetime.utcnow()
        embed.add_field(name="Member", value=f"{message.author} ({message.author.id})", inline=False)
        embed.add_field(name="Trigger", value=f"{threshold}+ messages in {window}s", inline=False)
        embed.add_field(name="Action taken", value=action_taken, inline=False)
        await _send_security_log(message.guild, settings, embed)

    # -----------------------------------------------------------------
    # /security commands - quick admin-facing toggles. Full configuration
    # (thresholds, banned-word list management, etc.) is also available from
    # the dashboard's upcoming Security page (next increment).
    # -----------------------------------------------------------------
    @security_group.command(name="status", description="Show the current security suite configuration")
    @app_commands.checks.has_permissions(administrator=True)
    async def status(self, interaction: discord.Interaction):
        settings = await get_guild_settings(interaction.guild_id)
        security = settings.get("security", {})
        embed = await build_embed(interaction.guild_id, title="🛡️ Security Suite Status", use_brand_thumbnail=False)
        for key, nice_name in (
            ("anti_nuke", "Anti-Nuke"),
            ("anti_spam", "Anti-Spam"),
            ("anti_link", "Anti-Link"),
            ("word_filter", "Word Filter"),
            ("anti_webhook", "Anti-Webhook"),
            ("raid_mode", "Raid Mode"),
        ):
            conf = security.get(key, {})
            state = "✅ ON" if conf.get("enabled") else "❌ OFF"
            embed.add_field(name=nice_name, value=state, inline=True)
        log_channel_id = security.get("log_channel_id")
        embed.add_field(
            name="Log Channel",
            value=f"<#{log_channel_id}>" if log_channel_id else "Not set (falls back to Warn Log)",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @security_group.command(name="toggle", description="Turn a security sub-system on or off")
    @app_commands.describe(system="Which sub-system", enabled="On or off")
    @app_commands.choices(
        system=[
            app_commands.Choice(name="Anti-Nuke", value="anti_nuke"),
            app_commands.Choice(name="Anti-Spam", value="anti_spam"),
            app_commands.Choice(name="Anti-Link", value="anti_link"),
            app_commands.Choice(name="Word Filter", value="word_filter"),
            app_commands.Choice(name="Anti-Webhook", value="anti_webhook"),
            app_commands.Choice(name="Raid Mode", value="raid_mode"),
        ]
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def toggle(self, interaction: discord.Interaction, system: app_commands.Choice[str], enabled: bool):
        await set_security_setting(interaction.guild_id, f"{system.value}.enabled", enabled)
        state = "enabled ✅" if enabled else "disabled ❌"
        await interaction.response.send_message(f"{system.name} {state}.", ephemeral=True)

    @security_group.command(name="log-channel", description="Set where security alerts are sent")
    @app_commands.checks.has_permissions(administrator=True)
    async def log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await set_security_setting(interaction.guild_id, "log_channel_id", channel.id)
        await interaction.response.send_message(f"Security alerts will now be sent to {channel.mention}.", ephemeral=True)

    @security_group.command(name="whitelist-add", description="Exempt a user from anti-nuke punishment")
    @app_commands.checks.has_permissions(administrator=True)
    async def whitelist_add(self, interaction: discord.Interaction, user: discord.Member):
        await add_security_whitelist_user(interaction.guild_id, user.id)
        await interaction.response.send_message(f"{user.mention} is now exempt from anti-nuke actions.", ephemeral=True)

    @security_group.command(name="whitelist-remove", description="Remove a user's anti-nuke exemption")
    @app_commands.checks.has_permissions(administrator=True)
    async def whitelist_remove(self, interaction: discord.Interaction, user: discord.Member):
        await remove_security_whitelist_user(interaction.guild_id, user.id)
        await interaction.response.send_message(f"Removed {user.mention}'s anti-nuke exemption.", ephemeral=True)

    @security_group.command(name="banned-word-add", description="Add a word to the word filter")
    @app_commands.checks.has_permissions(administrator=True)
    async def banned_word_add(self, interaction: discord.Interaction, word: str):
        await add_banned_word(interaction.guild_id, word)
        await interaction.response.send_message(f"Added `{word}` to the word filter.", ephemeral=True)

    @security_group.command(name="banned-word-remove", description="Remove a word from the word filter")
    @app_commands.checks.has_permissions(administrator=True)
    async def banned_word_remove(self, interaction: discord.Interaction, word: str):
        await remove_banned_word(interaction.guild_id, word)
        await interaction.response.send_message(f"Removed `{word}` from the word filter.", ephemeral=True)

    @security_group.command(name="link-whitelist-domain", description="Allow a domain through anti-link")
    @app_commands.checks.has_permissions(administrator=True)
    async def link_whitelist_domain(self, interaction: discord.Interaction, domain: str, remove: bool = False):
        if remove:
            await remove_link_whitelist_domain(interaction.guild_id, domain)
            await interaction.response.send_message(f"Removed `{domain}` from the link whitelist.", ephemeral=True)
        else:
            await add_link_whitelist_domain(interaction.guild_id, domain)
            await interaction.response.send_message(f"Whitelisted domain `{domain}`.", ephemeral=True)

    @security_group.command(name="link-whitelist-channel", description="Allow all links in a channel")
    @app_commands.checks.has_permissions(administrator=True)
    async def link_whitelist_channel(self, interaction: discord.Interaction, channel: discord.TextChannel, remove: bool = False):
        if remove:
            await remove_link_whitelist_channel(interaction.guild_id, channel.id)
            await interaction.response.send_message(f"Removed {channel.mention} from the link whitelist.", ephemeral=True)
        else:
            await add_link_whitelist_channel(interaction.guild_id, channel.id)
            await interaction.response.send_message(f"Links are now allowed in {channel.mention}.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Security(bot))
