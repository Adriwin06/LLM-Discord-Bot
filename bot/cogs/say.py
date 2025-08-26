import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional

class SayModal(discord.ui.Modal, title="Send a message as the bot"):
    def __init__(self, author_id: int, target_channel: discord.abc.Messageable):
        super().__init__()
        self.author_id = author_id
        self.target_channel = target_channel
        self.message_input = discord.ui.TextInput(
            label="Message",
            style=discord.TextStyle.paragraph,
            placeholder="Type the message to send (supports new lines & markdown)",
            required=True,
            max_length=2000
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        # Safety permission check
        if not interaction.user.guild_permissions.administrator or interaction.user.id != self.author_id:
            return await interaction.response.send_message("Not authorized.", ephemeral=True)
        try:
            await self.target_channel.send(self.message_input.value)
            await interaction.response.send_message(f"Message sent in {self.target_channel.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("No permission to send in that channel.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Failed to send message: {e}", ephemeral=True)

class Say(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="say", description="Make the bot send a message (omit message to open a multi-line modal).")
    @app_commands.describe(
        message="Optional quick single-line message. Leave empty for multi-line modal.",
        channel="Optional channel (defaults to current channel)"
    )
    async def say(
        self,
        interaction: discord.Interaction,
        message: Optional[str] = None,
        channel: Optional[discord.TextChannel] = None
    ):
        # Permission check
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "You must be an administrator to use this command.",
                ephemeral=True
            )

        target_channel = channel or interaction.channel
        if not isinstance(target_channel, (discord.TextChannel, discord.Thread)):
            return await interaction.response.send_message(
                "Cannot send message to that destination.",
                ephemeral=True
            )

        # If no message provided, present a modal for multi-line input
        if not message:
            modal = SayModal(interaction.user.id, target_channel)
            return await interaction.response.send_modal(modal)

        # Single-line path (existing behavior)
        await interaction.response.defer(ephemeral=True)
        try:
            await target_channel.send(message)
        except discord.Forbidden:
            return await interaction.followup.send(
                "I lack permission to send messages in that channel.",
                ephemeral=True
            )
        except Exception as e:
            return await interaction.followup.send(
                f"Failed to send message: {e}",
                ephemeral=True
            )

        await interaction.followup.send(
            f"Message sent in {target_channel.mention}.",
            ephemeral=True
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(Say(bot))
