# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/cogs/admin_commands.py
import discord
from discord.ext import commands
from discord import app_commands
import logging
from typing import Literal

class SummaryPaginationView(discord.ui.View):
    def __init__(self, summary_text: str, channel_name: str, last_update: str, messages_since: int, messages_processed: int, summary_type: str):
        super().__init__(timeout=300)  # 5 minute timeout
        self.summary_text = summary_text
        self.channel_name = channel_name
        self.last_update = last_update
        self.messages_since = messages_since
        self.messages_processed = messages_processed
        self.summary_type = summary_type
        self.current_page = 0
        
        # Split summary into pages (approximately 1000 chars per page to leave room for formatting)
        self.pages = []
        if len(summary_text) <= 1000:
            self.pages = [summary_text]
        else:
            # Split while preserving formatting
            lines = summary_text.split('\n')
            current_page = ""
            
            for line in lines:
                # Check if adding this line would exceed the limit
                if len(current_page) + len(line) + 1 > 1000:  # +1 for newline
                    if current_page:
                        self.pages.append(current_page.rstrip())
                        current_page = line
                    else:
                        # Line itself is too long, split it
                        if len(line) > 1000:
                            # Split long lines by character count
                            for i in range(0, len(line), 1000):
                                chunk = line[i:i+1000]
                                if i == 0 and current_page:
                                    current_page += "\n" + chunk
                                else:
                                    if current_page:
                                        self.pages.append(current_page.rstrip())
                                    current_page = chunk
                        else:
                            current_page = line
                else:
                    if current_page:
                        current_page += "\n" + line
                    else:
                        current_page = line
            
            if current_page:
                self.pages.append(current_page.rstrip())
        
        self.update_buttons()

    def update_buttons(self):
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= len(self.pages) - 1
        self.page_label.label = f"Page {self.current_page + 1}/{len(self.pages)}"

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, disabled=True)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        await self.update_embed(interaction)

    @discord.ui.button(label="Page 1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def page_label(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass  # This is just a label

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(len(self.pages) - 1, self.current_page + 1)
        self.update_buttons()
        await self.update_embed(interaction)

    async def update_embed(self, interaction: discord.Interaction):
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    def create_embed(self):
        embed = discord.Embed(
            title=f"Channel Summary: #{self.channel_name}",
            color=discord.Color.blue()
        )
        
        # Add summary text for current page
        page_text = self.pages[self.current_page] if self.current_page < len(self.pages) else ""
        embed.add_field(name=f"Summary (Page {self.current_page + 1})", value=page_text, inline=False)
        
        # Add metadata
        embed.add_field(name="Last Updated", value=self.last_update, inline=True)
        embed.add_field(name="Messages Since Update", value=str(self.messages_since), inline=True)
        embed.add_field(name="Messages Processed", value=str(self.messages_processed), inline=True)
        embed.add_field(name="Summary Type", value=self.summary_type.title(), inline=True)
        
        return embed

class AdminCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="set_llm", description="Configure core LLM parameters for the server.")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_llm(self, interaction: discord.Interaction, model: str, behavior_prompt: str, summarize_every_messages: int = 100, initial_summarize_messages: int = 1000, summarize_every_hours: int = 24):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild.id)
        
        settings = await self.bot.store.get_settings()
        if guild_id not in settings:
            settings[guild_id] = {}
            
        settings[guild_id].update({
            "model": model,
            "behavior_prompt": behavior_prompt,
            "summarize_every_messages": summarize_every_messages,
            "initial_summarize_messages": initial_summarize_messages,
            "summarize_every_hours": summarize_every_hours
        })
        
        await self.bot.store.save_settings(settings)
        await interaction.followup.send("✅ Server LLM settings updated successfully.", ephemeral=True)

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

        try:
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
            await interaction.followup.send(f"✅ Channel override for {channel.mention} updated.", ephemeral=True)
        except ValueError as e:
            await interaction.followup.send(f"❌ Invalid input: {str(e)}", ephemeral=True)
        except discord.errors.NotFound:
            await interaction.followup.send("❌ Channel not found.", ephemeral=True)
        except discord.errors.Forbidden:
            await interaction.followup.send("❌ Bot lacks permissions to access this channel.", ephemeral=True)
        except Exception as e:
            logging.error(f"Error in channel_override command: {str(e)}")
            await interaction.followup.send("❌ An unexpected error occurred while updating channel override.", ephemeral=True)

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
            
            # Create pagination view with full summary
            summary_text = summary_data["summary"]
            view = SummaryPaginationView(
                summary_text=summary_text,
                channel_name=target_channel.name,
                last_update=timestamp,
                messages_since=summary_data["messages_since_summary"],
                messages_processed=summary_data["messages_processed"],
                summary_type=summary_data["summary_type"]
            )
            
            embed = view.create_embed()
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            
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
                
        except ValueError as e:
            await interaction.followup.send(f"❌ Invalid input: {str(e)}", ephemeral=True)
        except discord.errors.NotFound:
            await interaction.followup.send("❌ Channel not found.", ephemeral=True)
        except discord.errors.Forbidden:
            await interaction.followup.send("❌ Bot lacks permissions to access this channel.", ephemeral=True)
        except Exception as e:
            logging.error(f"Error in summary_settings command: {str(e)}")
            await interaction.followup.send("❌ An unexpected error occurred while managing summary settings.", ephemeral=True)

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

    @app_commands.command(name="show_context", description="Show the complete context that would be sent to the LLM.")
    @app_commands.checks.has_permissions(administrator=True)
    async def show_context(self, interaction: discord.Interaction, 
                          message_id: str = None,
                          raw_format: bool = False,
                          include_bot_identity: bool = True,
                          include_channel_summary: bool = True,
                          include_user_profiles: bool = True,
                          include_conversation_history: bool = True,
                          include_reply_chain: bool = True,
                          include_current_message: bool = True):
        """
        Show the complete context that would be sent to the LLM.
        
        Parameters:
        - message_id: Optional message ID to build context from (defaults to latest message)
        - raw_format: Show the raw JSON format as sent to the AI, without formatting
        - Various include flags to control what context components to show
        """
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Get the target message
            target_message = None
            if message_id:
                try:
                    target_message = await interaction.channel.fetch_message(int(message_id))
                except (ValueError, discord.NotFound, discord.Forbidden):
                    await interaction.followup.send(f"❌ Could not find message with ID: {message_id}", ephemeral=True)
                    return
            else:
                # Get the latest message from the channel (excluding the command itself)
                async for message in interaction.channel.history(limit=10):
                    if message.id != interaction.id and not message.author.bot:
                        target_message = message
                        break
                
                if not target_message:
                    await interaction.followup.send("❌ No suitable message found to build context from.", ephemeral=True)
                    return
            
            # Build the context using the context manager
            context, settings = await self.bot.context_manager.build_context(
                message=target_message,
                prompt=None,
                behavior_override=None,
                capabilities_override=None,
                include_bot_identity=include_bot_identity,
                include_channel_summary=include_channel_summary,
                include_user_profiles=include_user_profiles,
                include_conversation_history=include_conversation_history,
                include_reply_chain=include_reply_chain,
                include_current_message=include_current_message
            )
            
            # Format the context for display
            if raw_format:
                # Show raw JSON format as sent to the AI
                import json
                context_text = "```json\n" + json.dumps(context, indent=2, ensure_ascii=False) + "\n```"
            else:
                # Show formatted version with role headers
                context_text = ""
                for item in context:
                    role = item.get("role", "unknown")
                    content = item.get("content", "")
                    
                    # Add role header
                    if role == "system":
                        context_text += "**[SYSTEM MESSAGE]**\n"
                    elif role == "user":
                        context_text += "**[USER MESSAGE]**\n"
                    elif role == "assistant":
                        context_text += "**[BOT (YOU)]**\n"
                    elif role == "bot (you)":
                        context_text += "**[BOT (YOU)]**\n"
                    else:
                        context_text += f"**[{role.upper()}]**\n"
                    
                    # Add content with some formatting
                    if len(content) > 500:
                        context_text += f"{content[:500]}...\n\n"
                    else:
                        context_text += f"{content}\n\n"
            
            # If context is too long, create a pagination view
            if len(context_text) > 1900:  # Leave room for embed formatting
                # Create pagination similar to summary view
                pages = []
                lines = context_text.split('\n')
                current_page = ""
                
                for line in lines:
                    if len(current_page) + len(line) + 1 > 1900:
                        if current_page:
                            pages.append(current_page.rstrip())
                            current_page = line
                        else:
                            # Line itself is too long
                            if len(line) > 1900:
                                for i in range(0, len(line), 1900):
                                    chunk = line[i:i+1900]
                                    pages.append(chunk)
                            else:
                                current_page = line
                    else:
                        if current_page:
                            current_page += "\n" + line
                        else:
                            current_page = line
                
                if current_page:
                    pages.append(current_page.rstrip())
                
                # Create a simple embed for the first page
                embed = discord.Embed(
                    title=f"LLM Context Debug (Page 1/{len(pages)})",
                    description=pages[0] if pages else "No context generated.",
                    color=discord.Color.purple()
                )
                
                embed.add_field(
                    name="Source Message",
                    value=f"ID: {target_message.id}\nAuthor: {target_message.author.display_name}\nChannel: #{target_message.channel.name}",
                    inline=False
                )
                
                embed.add_field(
                    name="Context Components",
                    value=f"Bot Identity: {'✅' if include_bot_identity else '❌'}\n"
                          f"Channel Summary: {'✅' if include_channel_summary else '❌'}\n"
                          f"User Profiles: {'✅' if include_user_profiles else '❌'}\n"
                          f"Conversation History: {'✅' if include_conversation_history else '❌'}\n"
                          f"Reply Chain: {'✅' if include_reply_chain else '❌'}\n"
                          f"Current Message: {'✅' if include_current_message else '❌'}",
                    inline=False
                )
                
                # Note: For now, just show the first page. A full pagination system would require more code.
                if len(pages) > 1:
                    embed.set_footer(text=f"Note: Context is {len(pages)} pages long. Only showing first page.")
                
                await interaction.followup.send(embed=embed, ephemeral=True)
                
                # If there are multiple pages, send additional messages
                if len(pages) > 1:
                    for i, page in enumerate(pages[1:], 2):
                        embed = discord.Embed(
                            title=f"LLM Context Debug (Page {i}/{len(pages)})",
                            description=page,
                            color=discord.Color.purple()
                        )
                        await interaction.followup.send(embed=embed, ephemeral=True)
                        
                        # Discord rate limiting - don't send too many messages at once
                        if i >= 5:  # Limit to 5 total messages
                            embed = discord.Embed(
                                title="Context Truncated",
                                description=f"Remaining {len(pages) - i} pages not shown to avoid rate limits.",
                                color=discord.Color.orange()
                            )
                            await interaction.followup.send(embed=embed, ephemeral=True)
                            break
            else:
                # Context fits in one message
                embed = discord.Embed(
                    title="LLM Context Debug",
                    description=context_text or "No context generated.",
                    color=discord.Color.purple()
                )
                
                embed.add_field(
                    name="Source Message",
                    value=f"ID: {target_message.id}\nAuthor: {target_message.author.display_name}\nChannel: #{target_message.channel.name}",
                    inline=False
                )
                
                embed.add_field(
                    name="Context Components",
                    value=f"Bot Identity: {'✅' if include_bot_identity else '❌'}\n"
                          f"Channel Summary: {'✅' if include_channel_summary else '❌'}\n"
                          f"User Profiles: {'✅' if include_user_profiles else '❌'}\n"
                          f"Conversation History: {'✅' if include_conversation_history else '❌'}\n"
                          f"Reply Chain: {'✅' if include_reply_chain else '❌'}\n"
                          f"Current Message: {'✅' if include_current_message else '❌'}",
                    inline=False
                )
                
                embed.add_field(
                    name="Settings Used",
                    value=f"Model: {settings.get('model', 'N/A')}\n"
                          f"Messages: {len(context)} total\n"
                          f"Context Length: {len(context_text)} chars",
                    inline=False
                )
                
                await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logging.error(f"Error in show_context command: {e}")
            await interaction.followup.send(f"❌ Error generating context: {str(e)}", ephemeral=True)

    @app_commands.command(name="reload", description="Reload various bot components.")
    @app_commands.checks.has_permissions(administrator=True)
    async def reload(self, interaction: discord.Interaction, 
                    component: Literal["cogs", "config", "prompts", "all"] = "all"):
        """
        Reload bot components without restarting.
        
        Parameters:
        - cogs: Reload all Discord cogs/commands
        - config: Reload configuration settings
        - prompts: Reload behavior and capability prompts
        - all: Reload everything
        """
        await interaction.response.defer(ephemeral=True)
        
        try:
            results = []
            
            if component in ["cogs", "all"]:
                # Reload all cogs
                cog_results = await self._reload_cogs()
                results.extend(cog_results)
            
            if component in ["config", "all"]:
                # Reload config
                config_result = await self._reload_config()
                results.append(config_result)
            
            if component in ["prompts", "all"]:
                # Reload prompts
                prompt_results = await self._reload_prompts()
                results.extend(prompt_results)
            
            # Format results
            success_count = sum(1 for r in results if r["success"])
            total_count = len(results)
            
            embed = discord.Embed(
                title=f"🔄 Reload Results ({success_count}/{total_count} successful)",
                color=discord.Color.green() if success_count == total_count else discord.Color.orange()
            )
            
            # Group results by type
            cog_results = [r for r in results if r["type"] == "cog"]
            config_results = [r for r in results if r["type"] == "config"]
            prompt_results = [r for r in results if r["type"] == "prompt"]
            
            if cog_results:
                cog_text = []
                for result in cog_results:
                    status = "✅" if result["success"] else "❌"
                    cog_text.append(f"{status} {result['name']}")
                    if not result["success"]:
                        cog_text.append(f"   └─ {result['error']}")
                
                embed.add_field(
                    name=f"Cogs ({sum(1 for r in cog_results if r['success'])}/{len(cog_results)})",
                    value="\n".join(cog_text) if cog_text else "None",
                    inline=False
                )
            
            if config_results:
                config_text = []
                for result in config_results:
                    status = "✅" if result["success"] else "❌"
                    config_text.append(f"{status} {result['name']}")
                    if not result["success"]:
                        config_text.append(f"   └─ {result['error']}")
                
                embed.add_field(
                    name="Configuration",
                    value="\n".join(config_text),
                    inline=False
                )
            
            if prompt_results:
                prompt_text = []
                for result in prompt_results:
                    status = "✅" if result["success"] else "❌"
                    prompt_text.append(f"{status} {result['name']}")
                    if not result["success"]:
                        prompt_text.append(f"   └─ {result['error']}")
                
                embed.add_field(
                    name="Prompts",
                    value="\n".join(prompt_text),
                    inline=False
                )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logging.error(f"Error in reload command: {e}")
            await interaction.followup.send(f"❌ Error during reload: {str(e)}", ephemeral=True)
    
    async def _reload_cogs(self):
        """Reload all cogs and return results."""
        results = []
        
        # Get list of currently loaded extensions
        extension_names = list(self.bot.extensions.keys())
        
        for extension_name in extension_names:
            try:
                # Skip reloading AdminCommands to avoid issues with the current command
                if "admin_commands" in extension_name:
                    continue
                
                # Try to reload
                await self.bot.reload_extension(extension_name)
                
                # Get a friendly name for display
                friendly_name = extension_name.split(".")[-1].replace("_", " ").title()
                
                results.append({
                    "type": "cog",
                    "name": friendly_name,
                    "success": True,
                    "error": None
                })
                
            except Exception as e:
                friendly_name = extension_name.split(".")[-1].replace("_", " ").title()
                results.append({
                    "type": "cog", 
                    "name": friendly_name,
                    "success": False,
                    "error": str(e)
                })
        
        # Try to reload AdminCommands last
        try:
            await self.bot.reload_extension("bot.cogs.admin_commands")
            results.append({
                "type": "cog",
                "name": "Admin Commands",
                "success": True,
                "error": None
            })
        except Exception as e:
            results.append({
                "type": "cog",
                "name": "Admin Commands", 
                "success": False,
                "error": str(e)
            })
        
        return results
    
    async def _reload_config(self):
        """Reload configuration and return result."""
        try:
            # Reload the config module
            import importlib
            from bot import config
            importlib.reload(config)
            
            # Update bot's config reference
            self.bot.config = config
            
            return {
                "type": "config",
                "name": "Configuration",
                "success": True,
                "error": None
            }
        except Exception as e:
            return {
                "type": "config",
                "name": "Configuration",
                "success": False,
                "error": str(e)
            }
    
    async def _reload_prompts(self):
        """Reload prompt files and return results."""
        results = []
        
        try:
            import os
            
            # Reload behavior prompt
            behavior_path = os.path.join("prompts", "BEHAVIOR_PROMPT.md")
            if os.path.exists(behavior_path):
                try:
                    with open(behavior_path, 'r', encoding='utf-8') as f:
                        f.read()  # Just verify we can read the file
                    
                    # Update in bot (if there's a way to do this)
                    # This depends on how your bot stores default prompts
                    results.append({
                        "type": "prompt",
                        "name": "Behavior Prompt",
                        "success": True,
                        "error": None
                    })
                except Exception as e:
                    results.append({
                        "type": "prompt",
                        "name": "Behavior Prompt",
                        "success": False,
                        "error": str(e)
                    })
            
            # Reload capabilities prompt
            capabilities_path = os.path.join("prompts", "CAPABILITIES_PROMPT.md")
            if os.path.exists(capabilities_path):
                try:
                    with open(capabilities_path, 'r', encoding='utf-8') as f:
                        f.read()  # Just verify we can read the file
                    
                    results.append({
                        "type": "prompt",
                        "name": "Capabilities Prompt", 
                        "success": True,
                        "error": None
                    })
                except Exception as e:
                    results.append({
                        "type": "prompt",
                        "name": "Capabilities Prompt",
                        "success": False,
                        "error": str(e)
                    })
            
        except Exception as e:
            results.append({
                "type": "prompt",
                "name": "Prompts",
                "success": False,
                "error": str(e)
            })
        
        return results

    @app_commands.command(name="restart", description="Restart the bot (requires administrator permissions).")
    @app_commands.checks.has_permissions(administrator=True)
    async def restart(self, interaction: discord.Interaction):
        """
        Restart the bot within its virtual environment.
        This will close the current bot process and start a new one.
        """
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Send confirmation message
            await interaction.followup.send("🔄 **Restarting bot...** The bot will be back online shortly.", ephemeral=True)
            
            # Log the restart
            logging.info(f"Bot restart initiated by {interaction.user} ({interaction.user.id}) in guild {interaction.guild.name} ({interaction.guild.id})")
            
            # Give a moment for the message to send
            import asyncio
            await asyncio.sleep(1)
            
            # Restart the bot
            import sys
            import os
            import subprocess
            
            # Try to detect the virtual environment and restart appropriately
            python_executable = sys.executable
            script_path = os.path.abspath(sys.argv[0])
            
            # If we're in a virtual environment, use the venv's python
            if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
                # We're in a virtual environment
                logging.info(f"Restarting in virtual environment using: {python_executable}")
                
                # Start new process
                if os.name == 'nt':  # Windows
                    # Use CREATE_NEW_PROCESS_GROUP to detach from current process
                    subprocess.Popen([python_executable, script_path], 
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                                   cwd=os.path.dirname(script_path))
                else:  # Unix-like (Linux, macOS)
                    subprocess.Popen([python_executable, script_path],
                                   cwd=os.path.dirname(script_path),
                                   preexec_fn=os.setsid)
            else:
                # Not in a virtual environment, just restart with current python
                logging.info(f"Restarting with system Python: {python_executable}")
                
                if os.name == 'nt':  # Windows
                    subprocess.Popen([python_executable, script_path],
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                                   cwd=os.path.dirname(script_path))
                else:  # Unix-like
                    subprocess.Popen([python_executable, script_path],
                                   cwd=os.path.dirname(script_path),
                                   preexec_fn=os.setsid)
            
            # Close the current bot instance
            logging.info("Shutting down current bot instance...")
            await self.bot.close()
            
        except Exception as e:
            logging.error(f"Error during restart: {e}")
            try:
                await interaction.edit_original_response(content=f"❌ **Failed to restart bot:** {str(e)}")
            except Exception:
                # If we can't edit the response, the bot might already be shutting down
                pass

async def setup(bot):
    await bot.add_cog(AdminCommands(bot))
