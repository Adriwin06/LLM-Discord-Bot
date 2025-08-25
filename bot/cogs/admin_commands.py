# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/cogs/admin_commands.py
import discord
from discord.ext import commands
from discord import app_commands
import logging
from typing import Literal

class AdminCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="set_llm", description="Configure core LLM parameters for the server.")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_llm(self, interaction: discord.Interaction, model: str, behavior_prompt: str, summarize_every_messages: int = 100, summarize_every_hours: int = 24):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild.id)
        
        settings = await self.bot.store.get_settings()
        if guild_id not in settings:
            settings[guild_id] = {}
            
        settings[guild_id].update({
            "model": model,
            "behavior_prompt": behavior_prompt,
            "summarize_every_messages": summarize_every_messages,
            "summarize_every_hours": summarize_every_hours
        })
        
        await self.bot.store.save_settings(settings)
        await interaction.followup.send("Server LLM settings updated successfully.", ephemeral=True)

    media_config_group = app_commands.Group(name="media_config", description="Configure media processing settings for the server.")

    @media_config_group.command(name="set", description="Set a specific media processing setting.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        media_type="The type of media to configure.",
        setting="The setting to change for this media type.",
        value="The new value for the setting (e.g., 'true', 'false', '10')."
    )
    async def set_media_config(self, interaction: discord.Interaction, 
                               media_type: Literal["images", "audio", "video", "pdf", "office_documents", "text_files", "other_files"],
                               setting: str,
                               value: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild.id)

        settings = await self.bot.store.get_settings()
        if guild_id not in settings:
            settings[guild_id] = {}
        if "media" not in settings[guild_id]:
            settings[guild_id]["media"] = {}
        if media_type not in settings[guild_id]["media"]:
            settings[guild_id]["media"][media_type] = {}

        # Basic type conversion
        try:
            if value.lower() == "true":
                final_value = True
            elif value.lower() == "false":
                final_value = False
            elif value.isdigit():
                final_value = int(value)
            else:
                final_value = float(value)
        except ValueError:
            final_value = value # Keep as string if conversion fails

        settings[guild_id]["media"][media_type][setting] = final_value
        
        await self.bot.store.save_settings(settings)
        await interaction.followup.send(f"Media config for `{media_type}` updated: set `{setting}` to `{final_value}`.", ephemeral=True)

    @media_config_group.command(name="view", description="View the current media processing settings for the server.")
    @app_commands.checks.has_permissions(administrator=True)
    async def view_media_config(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild.id)
        
        settings = await self.bot.store.get_guild_settings(guild_id)
        media_settings = settings.get("media", {})

        embed = discord.Embed(title="Media Processing Configuration", color=discord.Color.orange())
        
        if not media_settings:
            embed.description = "No custom media settings found. Using bot defaults."
        else:
            for media_type, config in media_settings.items():
                value_str = "\n".join([f"**{key}:** `{value}`" for key, value in config.items()])
                if not value_str:
                    value_str = "No settings configured."
                embed.add_field(name=media_type.replace("_", " ").title(), value=value_str, inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="channel_override", description="Override global settings for a specific channel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def channel_override(self, interaction: discord.Interaction, channel: discord.TextChannel, model: str = None, behavior_prompt: str = None, summarize_every_messages: int = None):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild.id)
        channel_id = str(channel.id)

        settings = await self.bot.store.get_settings()
        if guild_id not in settings:
            settings[guild_id] = {}
        if "channel_overrides" not in settings[guild_id]:
            settings[guild_id]["channel_overrides"] = {}
        if channel_id not in settings[guild_id]["channel_overrides"]:
            settings[guild_id]["channel_overrides"][channel_id] = {}

        override_settings = {}
        if model:
            override_settings["model"] = model
        if behavior_prompt:
            override_settings["behavior_prompt"] = behavior_prompt
        if summarize_every_messages:
            override_settings["summarize_every_messages"] = summarize_every_messages
            
        settings[guild_id]["channel_overrides"][channel_id].update(override_settings)
        
        await self.bot.store.save_settings(settings)
        await interaction.followup.send(f"Channel override for {channel.mention} updated.", ephemeral=True)

    @app_commands.command(name="reset_context", description="Reset the bot's context for a channel or the entire server.")
    @app_commands.checks.has_permissions(administrator=True)
    async def reset_context(self, interaction: discord.Interaction, target: str = "channel"):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild.id)
        
        fresh_data = await self.bot.store.get_data()
        
        if target.lower() == "channel":
            channel_id = str(interaction.channel.id)
            if (str(guild_id) in fresh_data and 
                "channels" in fresh_data[str(guild_id)] and 
                channel_id in fresh_data[str(guild_id)]["channels"]):
                
                fresh_data[str(guild_id)]["channels"][channel_id].pop("summary", None)
                fresh_data[str(guild_id)]["channels"][channel_id]["messages_since_summary"] = 0
                await self.bot.store.save_data(fresh_data)
                await interaction.followup.send("Context for this channel has been reset.", ephemeral=True)
            else:
                await interaction.followup.send("No context to reset for this channel.", ephemeral=True)
        elif target.lower() == "guild":
            if str(guild_id) in fresh_data and "channels" in fresh_data[str(guild_id)]:
                for channel_data in fresh_data[str(guild_id)]["channels"].values():
                    channel_data.pop("summary", None)
                    channel_data["messages_since_summary"] = 0
                await self.bot.store.save_data(fresh_data)
                await interaction.followup.send("Context for the entire server has been reset.", ephemeral=True)
            else:
                await interaction.followup.send("No context to reset for this server.", ephemeral=True)
        else:
            await interaction.followup.send("Invalid target. Use 'channel' or 'guild'.", ephemeral=True)

    @app_commands.command(name="backup", description="Manually trigger a backup of the bot's data.")
    @app_commands.checks.has_permissions(administrator=True)
    async def backup(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        logging.info(f"Manual backup triggered by {interaction.user}")
        await self.bot.store.backup_data()
        await interaction.followup.send("Backup completed successfully.", ephemeral=True)

    @app_commands.command(name="model_info", description="Check capabilities of a specific model.")
    @app_commands.checks.has_permissions(administrator=True)
    async def model_info(self, interaction: discord.Interaction, model: str):
        await interaction.response.defer(ephemeral=True)
        
        try:
            capabilities = self.bot.llm_provider.get_model_capabilities(model)
            
            embed = discord.Embed(
                title=f"Model Capabilities: {model}",
                color=discord.Color.blue()
            )
            
            embed.add_field(name="Vision Support", value="✅ Yes" if capabilities["vision"] else "❌ No", inline=True)
            embed.add_field(name="Audio Support", value="✅ Yes" if capabilities["audio"] else "❌ No", inline=True)
            embed.add_field(name="PDF Support", value="✅ Yes" if capabilities["pdf"] else "❌ No", inline=True)
            embed.add_field(name="Web Search", value="✅ Yes" if capabilities["web_search"] else "❌ No", inline=True)
            
            # Add web search config info if supported
            if capabilities["web_search"]:
                web_search_status = "✅ Enabled" if self.bot.config.WEB_SEARCH_ENABLED else "⚠️ Disabled in Config"
                embed.add_field(
                    name="Web Search Config", 
                    value=f"Status: {web_search_status}\nContext Size: {self.bot.config.WEB_SEARCH_CONTEXT_SIZE}", 
                    inline=False
                )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            await interaction.followup.send(f"Error checking model capabilities: {str(e)}", ephemeral=True)

    summary_group = app_commands.Group(name="summary", description="Manage channel summaries.")

    @summary_group.command(name="view", description="View the current summary for a channel.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def view_summary(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        
        target_channel = channel or interaction.channel
        guild_id = str(interaction.guild.id)
        channel_id = str(target_channel.id)
        
        try:
            summary_data = await self.bot.context_manager.get_channel_summary(guild_id, channel_id)
            
            embed = discord.Embed(
                title=f"Channel Summary: #{target_channel.name}",
                color=discord.Color.blue()
            )
            
            # Add summary text
            summary_text = summary_data["summary"]
            if len(summary_text) > 1024:
                summary_text = summary_text[:1021] + "..."
            embed.add_field(name="Summary", value=summary_text, inline=False)
            
            # Add metadata
            last_update = summary_data.get("last_summary_time")
            if last_update:
                try:
                    from datetime import datetime
                    update_time = datetime.fromisoformat(last_update)
                    timestamp = f"<t:{int(update_time.timestamp())}:R>"
                except Exception:
                    timestamp = last_update
            else:
                timestamp = "Never"
            
            embed.add_field(name="Last Updated", value=timestamp, inline=True)
            embed.add_field(name="Messages Since Update", value=str(summary_data["messages_since_summary"]), inline=True)
            embed.add_field(name="Messages Processed", value=str(summary_data["messages_processed"]), inline=True)
            embed.add_field(name="Summary Type", value=summary_data["summary_type"].title(), inline=True)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            await interaction.followup.send(f"Error retrieving channel summary: {str(e)}", ephemeral=True)

    @summary_group.command(name="update", description="Force an immediate update of a channel's summary.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def update_summary(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        
        target_channel = channel or interaction.channel
        guild_id = str(interaction.guild.id)
        channel_id = str(target_channel.id)
        
        try:
            # Show thinking message
            await interaction.followup.send(f"🔄 Updating summary for #{target_channel.name}... This may take a moment.", ephemeral=True)
            
            success = await self.bot.context_manager.force_channel_summary_update(guild_id, channel_id)
            
            if success:
                await interaction.edit_original_response(content=f"✅ Successfully updated summary for #{target_channel.name}!")
            else:
                await interaction.edit_original_response(content=f"❌ Failed to update summary for #{target_channel.name}. Check logs for details.")
                
        except Exception as e:
            await interaction.edit_original_response(content=f"❌ Error updating channel summary: {str(e)}")

    @summary_group.command(name="clear", description="Clear a channel's summary and reset counters.")
    @app_commands.checks.has_permissions(administrator=True)
    async def clear_summary(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        
        target_channel = channel or interaction.channel
        guild_id = str(interaction.guild.id)
        channel_id = str(target_channel.id)
        
        try:
            success = await self.bot.context_manager.clear_channel_summary(guild_id, channel_id)
            
            if success:
                await interaction.followup.send(f"✅ Cleared summary for #{target_channel.name} and reset counters.", ephemeral=True)
            else:
                await interaction.followup.send(f"ℹ️ No summary found for #{target_channel.name} to clear.", ephemeral=True)
                
        except Exception as e:
            await interaction.followup.send(f"❌ Error clearing channel summary: {str(e)}", ephemeral=True)

    @summary_group.command(name="settings", description="View or modify summary settings for a channel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def summary_settings(self, interaction: discord.Interaction, 
                              channel: discord.TextChannel = None,
                              summarize_every_messages: int = None,
                              summarize_every_hours: int = None):
        await interaction.response.defer(ephemeral=True)
        
        target_channel = channel or interaction.channel
        guild_id = str(interaction.guild.id)
        channel_id = str(target_channel.id)
        
        try:
            if summarize_every_messages is not None or summarize_every_hours is not None:
                # Update settings
                settings = await self.bot.store.get_settings()
                if guild_id not in settings:
                    settings[guild_id] = {}
                if "channel_overrides" not in settings[guild_id]:
                    settings[guild_id]["channel_overrides"] = {}
                if channel_id not in settings[guild_id]["channel_overrides"]:
                    settings[guild_id]["channel_overrides"][channel_id] = {}
                
                if summarize_every_messages is not None:
                    settings[guild_id]["channel_overrides"][channel_id]["summarize_every_messages"] = summarize_every_messages
                if summarize_every_hours is not None:
                    settings[guild_id]["channel_overrides"][channel_id]["summarize_every_hours"] = summarize_every_hours
                
                await self.bot.store.save_settings(settings)
                await interaction.followup.send(f"✅ Updated summary settings for #{target_channel.name}.", ephemeral=True)
            else:
                # View current settings
                current_settings = await self.bot.context_manager.get_guild_and_channel_settings(guild_id, channel_id)
                
                embed = discord.Embed(
                    title=f"Summary Settings: #{target_channel.name}",
                    color=discord.Color.orange()
                )
                
                embed.add_field(
                    name="Messages Trigger",
                    value=f"{current_settings.get('summarize_every_messages', self.bot.config.DEFAULT_SUMMARIZE_EVERY_MESSAGES)} messages",
                    inline=True
                )
                embed.add_field(
                    name="Time Trigger", 
                    value=f"{current_settings.get('summarize_every_hours', self.bot.config.DEFAULT_SUMMARIZE_EVERY_HOURS)} hours",
                    inline=True
                )
                embed.add_field(
                    name="Model",
                    value=current_settings.get('model', self.bot.config.MAIN_LLM_MODEL),
                    inline=False
                )
                
                await interaction.followup.send(embed=embed, ephemeral=True)
                
        except Exception as e:
            await interaction.followup.send(f"❌ Error managing summary settings: {str(e)}", ephemeral=True)

    @app_commands.command(name="user_profile", description="Manage user profile settings and statistics.")
    @app_commands.checks.has_permissions(administrator=True)
    async def user_profile(self, interaction: discord.Interaction, 
                          action: Literal["stats", "update", "settings"],
                          user: discord.Member = None,
                          profile_update_every_messages: int = None,
                          profile_update_every_hours: int = None):
        """
        Manage user profile system.
        - stats: Show profile statistics for the server
        - update: Force update a specific user's profile  
        - settings: Configure profile update triggers
        """
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild.id)
        
        try:
            if action == "stats":
                # Show profile statistics
                data = await self.bot.store.get_data()
                guild_data = data.get(guild_id, {})
                users_data = guild_data.get("users", {})
                
                total_users = len(users_data)
                users_with_ai_summary = sum(1 for user_data in users_data.values() if user_data.get("ai_summary"))
                users_with_manual_notes = sum(1 for user_data in users_data.values() if user_data.get("manual_note"))
                
                # Calculate average messages since last update
                total_messages_pending = sum(user_data.get("messages_since_profile_update", 0) for user_data in users_data.values())
                avg_messages_pending = total_messages_pending / max(total_users, 1)
                
                embed = discord.Embed(
                    title="📊 User Profile Statistics",
                    color=discord.Color.blue()
                )
                
                embed.add_field(name="Total Users Tracked", value=str(total_users), inline=True)
                embed.add_field(name="Users with AI Summary", value=str(users_with_ai_summary), inline=True)
                embed.add_field(name="Users with Manual Notes", value=str(users_with_manual_notes), inline=True)
                embed.add_field(name="Avg Messages Pending Update", value=f"{avg_messages_pending:.1f}", inline=True)
                
                # Show top users by message count since last update
                top_users = sorted(
                    [(user_id, user_data.get("messages_since_profile_update", 0)) 
                     for user_id, user_data in users_data.items()],
                    key=lambda x: x[1], reverse=True
                )[:5]
                
                if top_users:
                    top_users_text = []
                    for user_id, msg_count in top_users:
                        try:
                            user_obj = interaction.guild.get_member(int(user_id))
                            name = user_obj.display_name if user_obj else f"User {user_id}"
                            top_users_text.append(f"{name}: {msg_count} messages")
                        except Exception:
                            top_users_text.append(f"User {user_id}: {msg_count} messages")
                    
                    embed.add_field(
                        name="Top Users Needing Updates",
                        value="\n".join(top_users_text),
                        inline=False
                    )
                
                await interaction.followup.send(embed=embed, ephemeral=True)
                
            elif action == "update":
                if not user:
                    await interaction.followup.send("❌ You must specify a user to update.", ephemeral=True)
                    return
                
                # Force update the user's profile
                await interaction.followup.send(f"🔄 Starting profile update for {user.display_name}...", ephemeral=True)
                
                await self.bot.context_manager.update_user_profile(guild_id, str(user.id), interaction.guild)
                
                await interaction.edit_original_response(content=f"✅ Profile update completed for {user.display_name}.")
                
            elif action == "settings":
                settings = await self.bot.store.get_settings()
                if guild_id not in settings:
                    settings[guild_id] = {}
                
                # Update settings if provided
                if profile_update_every_messages is not None:
                    settings[guild_id]["profile_update_every_messages"] = profile_update_every_messages
                if profile_update_every_hours is not None:
                    settings[guild_id]["profile_update_every_hours"] = profile_update_every_hours
                
                if profile_update_every_messages is not None or profile_update_every_hours is not None:
                    await self.bot.store.save_settings(settings)
                    await interaction.followup.send("✅ Profile update settings saved.", ephemeral=True)
                else:
                    # Just show current settings
                    current_settings = settings.get(guild_id, {})
                    
                    embed = discord.Embed(
                        title="⚙️ Profile Update Settings",
                        color=discord.Color.orange()
                    )
                    
                    embed.add_field(
                        name="Messages Trigger",
                        value=f"{current_settings.get('profile_update_every_messages', self.bot.config.DEFAULT_PROFILE_UPDATE_EVERY_MESSAGES)} messages",
                        inline=True
                    )
                    embed.add_field(
                        name="Time Trigger", 
                        value=f"{current_settings.get('profile_update_every_hours', self.bot.config.DEFAULT_PROFILE_UPDATE_EVERY_HOURS)} hours",
                        inline=True
                    )
                    
                    await interaction.followup.send(embed=embed, ephemeral=True)
                
        except Exception as e:
            logging.error(f"Error in user_profile command: {e}")
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(AdminCommands(bot))
