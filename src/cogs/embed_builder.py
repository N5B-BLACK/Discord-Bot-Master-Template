"""
/embed command group - the Discord-side half of the Embed Builder feature.

Full control (all fields, author, thumbnail, image, timestamp, up to 25 fields) only
really fits in the dashboard - Discord Modals cap out at 5 inputs, so there's no way
to expose everything in a single popup. The split:
- /embed quick   -> a 5-field modal (title, description, color, image, footer) for a
                     fast one-off embed, saved as a draft under the given name.
- /embed builder -> hands back a link into the web dashboard's full builder (every
                     field, live preview) for anything more advanced.
- /embed send / list / preview / delete -> manage saved drafts from Discord once
                     they exist (built via either path above).

Drafts are shared storage (utils/db.py embed_drafts collection) - build it in the
dashboard, send it with /embed send, or vice versa; both read/write the same data.
"""

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.db import delete_embed_draft, get_embed_draft, list_embed_drafts, save_embed_draft
from utils.embed_builder import EmbedValidationError, blank_embed_json, to_discord_embed

MAX_DRAFTS_SUGGESTED = 25


async def _draft_name_autocomplete(interaction: discord.Interaction, current: str):
    drafts = await list_embed_drafts(interaction.guild_id)
    names = [d["name"] for d in drafts]
    matches = [n for n in names if current.lower() in n.lower()][:25]
    return [app_commands.Choice(name=n, value=n) for n in matches]


class QuickEmbedModal(discord.ui.Modal, title="Quick Embed"):
    embed_title = discord.ui.TextInput(label="Title", required=False, max_length=256)
    description = discord.ui.TextInput(
        label="Description", style=discord.TextStyle.paragraph, required=False, max_length=4000
    )
    color_hex = discord.ui.TextInput(
        label="Color (hex, e.g. 7c5cff)", required=False, max_length=6, placeholder="5865F2"
    )
    image_url = discord.ui.TextInput(label="Image URL", required=False)
    footer_text = discord.ui.TextInput(label="Footer text", required=False, max_length=2048)

    def __init__(self, draft_name: str):
        super().__init__()
        self.draft_name = draft_name

    async def on_submit(self, interaction: discord.Interaction):
        data = blank_embed_json()
        data["title"] = self.embed_title.value or ""
        data["description"] = self.description.value or ""
        data["image_url"] = self.image_url.value or ""
        data["footer_text"] = self.footer_text.value or ""
        if self.color_hex.value:
            try:
                data["color"] = int(self.color_hex.value.strip().lstrip("#"), 16)
            except ValueError:
                await interaction.response.send_message(
                    "⚠️ Invalid color hex - saved with the default color instead.", ephemeral=True
                )
                data["color"] = 0x5865F2

        try:
            embed = to_discord_embed(data)
        except EmbedValidationError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return

        await save_embed_draft(interaction.guild_id, self.draft_name, data, interaction.user.id)
        await interaction.response.send_message(
            f"✅ Saved as **{self.draft_name}**. Use `/embed send` to post it, or `/embed preview` to check it first.",
            embed=embed,
            ephemeral=True,
        )


class EmbedGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="embed", description="Build and send fully custom embeds")


class EmbedBuilder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.group = EmbedGroup()
        self._register_commands()
        bot.tree.add_command(self.group)

    def _register_commands(self):
        group = self.group

        @group.command(name="quick", description="Build a simple embed via a quick popup form")
        @app_commands.describe(name="A short name to save this draft under (e.g. 'rules')")
        @app_commands.checks.has_permissions(manage_guild=True)
        async def quick(interaction: discord.Interaction, name: str):
            await interaction.response.send_modal(QuickEmbedModal(name))

        @group.command(name="builder", description="Open the full embed builder (all fields, live preview) in the dashboard")
        @app_commands.checks.has_permissions(manage_guild=True)
        async def builder(interaction: discord.Interaction):
            if not config.DASHBOARD_BASE_URL:
                await interaction.response.send_message(
                    "⚠️ The dashboard isn't configured on this deployment (missing REDIRECT_URI).",
                    ephemeral=True,
                )
                return
            url = f"{config.DASHBOARD_BASE_URL}/dashboard/{interaction.guild_id}/embeds"
            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="Open Embed Builder", url=url, style=discord.ButtonStyle.link))
            await interaction.response.send_message(
                "Full control over every field (author, thumbnail, image, up to 25 fields, live preview) lives in the dashboard:",
                view=view,
                ephemeral=True,
            )

        @group.command(name="send", description="Send a saved embed draft to a channel")
        @app_commands.describe(name="The saved draft's name", channel="Where to send it (defaults to this channel)")
        @app_commands.autocomplete(name=_draft_name_autocomplete)
        @app_commands.checks.has_permissions(manage_guild=True)
        async def send(interaction: discord.Interaction, name: str, channel: discord.TextChannel = None):
            draft = await get_embed_draft(interaction.guild_id, name)
            if not draft:
                await interaction.response.send_message(f"⚠️ No draft named **{name}** found.", ephemeral=True)
                return

            try:
                embed = to_discord_embed(draft["embed_json"])
            except EmbedValidationError as e:
                await interaction.response.send_message(f"⚠️ Can't send - {e}", ephemeral=True)
                return

            target = channel or interaction.channel
            try:
                await target.send(embed=embed)
            except discord.Forbidden:
                await interaction.response.send_message(
                    f"⚠️ I don't have permission to send messages in {target.mention}.", ephemeral=True
                )
                return
            await interaction.response.send_message(f"✅ Sent **{name}** to {target.mention}.", ephemeral=True)

        @group.command(name="preview", description="Preview a saved embed draft (only visible to you)")
        @app_commands.describe(name="The saved draft's name")
        @app_commands.autocomplete(name=_draft_name_autocomplete)
        async def preview(interaction: discord.Interaction, name: str):
            draft = await get_embed_draft(interaction.guild_id, name)
            if not draft:
                await interaction.response.send_message(f"⚠️ No draft named **{name}** found.", ephemeral=True)
                return
            try:
                embed = to_discord_embed(draft["embed_json"])
            except EmbedValidationError as e:
                await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
                return
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @group.command(name="list", description="List this server's saved embed drafts")
        async def list_drafts(interaction: discord.Interaction):
            drafts = await list_embed_drafts(interaction.guild_id)
            if not drafts:
                await interaction.response.send_message(
                    "No saved embeds yet. Try `/embed quick` or `/embed builder`.", ephemeral=True
                )
                return
            lines = [f"• **{d['name']}**" for d in drafts[:MAX_DRAFTS_SUGGESTED]]
            await interaction.response.send_message(
                "📋 Saved embed drafts:\n" + "\n".join(lines), ephemeral=True
            )

        @group.command(name="delete", description="Delete a saved embed draft")
        @app_commands.describe(name="The saved draft's name")
        @app_commands.autocomplete(name=_draft_name_autocomplete)
        @app_commands.checks.has_permissions(manage_guild=True)
        async def delete(interaction: discord.Interaction, name: str):
            draft = await get_embed_draft(interaction.guild_id, name)
            if not draft:
                await interaction.response.send_message(f"⚠️ No draft named **{name}** found.", ephemeral=True)
                return
            await delete_embed_draft(interaction.guild_id, name)
            await interaction.response.send_message(f"🗑️ Deleted **{name}**.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(EmbedBuilder(bot))
