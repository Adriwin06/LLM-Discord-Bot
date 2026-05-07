# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/cogs/profile_commands.py
import discord
from discord.ext import commands
from discord import app_commands
import logging
from .utilities import AdvancedPaginationView

class ProfileCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    note_group = app_commands.Group(name="note", description="Commands for managing user profiles.")
    note_refresh_group = app_commands.Group(name="refresh", description="Refresh user profile data.", parent=note_group)

    @note_group.command(name="add", description="Adds a permanent manual note to a user.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def add_note(self, interaction: discord.Interaction, user: discord.Member, note: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild.id)
        user_id = str(user.id)

        fresh_data = await self.bot.store.get_data()
        if str(guild_id) not in fresh_data:
            fresh_data[str(guild_id)] = {}
        if "users" not in fresh_data[str(guild_id)]:
            fresh_data[str(guild_id)]["users"] = {}
        if user_id not in fresh_data[str(guild_id)]["users"]:
            fresh_data[str(guild_id)]["users"][user_id] = {}
            
        fresh_data[str(guild_id)]["users"][user_id]["manual_note"] = note
        
        await self.bot.store.save_data(fresh_data)
        await interaction.followup.send(f"Note added for {user.display_name}.", ephemeral=True)

    @note_group.command(name="view", description="Displays the profile for a user.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def view_note(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild.id)
        user_id = str(user.id)

        data = await self.bot.store.get_data()
        user_data = data.get(guild_id, {}).get("users", {}).get(user_id, {})

        if not user_data:
            await interaction.followup.send(f"No profile data found for {user.display_name}.", ephemeral=True)
            return

        embed = discord.Embed(title=f"Profile for {user.display_name}", color=discord.Color.blue())
        
        manual_note = user_data.get("manual_note")
        ai_summary = user_data.get("ai_summary")
        last_update = user_data.get("last_profile_update_time")
        msg_count = user_data.get("messages_since_profile_update", 0)
        
        # Manual note field
        if manual_note:
            embed.add_field(name="Manual Note", value=manual_note, inline=False)
        else:
            embed.add_field(name="Manual Note", value="No manual note set.", inline=False)
        
        # AI summary field
        if ai_summary:
            # Truncate if too long for Discord embed
            if len(ai_summary) > 1024:
                ai_summary_display = ai_summary[:1021] + "..."
            else:
                ai_summary_display = ai_summary
            embed.add_field(name="AI-Generated Summary", value=ai_summary_display, inline=False)
        else:
            embed.add_field(name="AI-Generated Summary", value="No AI summary available yet.", inline=False)
        
        # Add metadata
        metadata_parts = []
        if last_update:
            try:
                from datetime import datetime
                update_time = datetime.fromisoformat(last_update)
                metadata_parts.append(f"Last AI update: {update_time.strftime('%Y-%m-%d %H:%M UTC')}")
            except Exception:
                metadata_parts.append("Last AI update: Unknown")
        else:
            metadata_parts.append("Last AI update: Never")
            
        metadata_parts.append(f"Messages since last update: {msg_count}")
        profile_text = (
            f"**Manual Note**\n{manual_note or 'No manual note set.'}\n\n"
            f"**AI-Generated Summary**\n{ai_summary or 'No AI summary available yet.'}\n\n"
            f"**Profile Info**\n" + "\n".join(metadata_parts)
        )
        needs_pagination = len(profile_text) > 1800
        if manual_note and len(manual_note) > 1024:
            needs_pagination = True
        if ai_summary and len(ai_summary) > 1024:
            needs_pagination = True

        if needs_pagination:
            await AdvancedPaginationView.send_paginated_text(
                interaction=interaction,
                content=profile_text,
                title=f"Profile for {user.display_name}",
                color=discord.Color.blue(),
                ephemeral=True
            )
            return

        embed.add_field(name="Profile Info", value="\n".join(metadata_parts), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    @note_refresh_group.command(name="ai", description="Forces an update of the AI-generated summary for a user.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def refresh_ai_note(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)
        
        logging.info(f"AI summary refresh triggered for {user.display_name} by {interaction.user}")
        
        try:
            # Send initial status message
            await interaction.followup.send(f"🔄 Updating AI summary for {user.display_name}...", ephemeral=True)
            
            # Perform the actual update
            await self.bot.context_manager.update_user_profile(interaction.guild.id, user.id, interaction.guild)
            
            # Send completion message
            await interaction.followup.send(f"✅ AI summary refresh for {user.display_name} completed successfully!", ephemeral=True)
            
        except Exception as e:
            logging.error(f"Error refreshing AI summary for {user.display_name}: {e}")
            await interaction.followup.send(f"❌ Failed to refresh AI summary for {user.display_name}. Check logs for details.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(ProfileCommands(bot))
