# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/cogs/admin_commands.py
import discord
from discord.ext import commands
from discord import app_commands
import logging
import os
import sys
from typing import Literal, Optional
from .utilities import AdvancedPaginationView, MessageChunker

class AdminCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    llm_group = app_commands.Group(name="llm", description="Manage LLM settings.")
    media_group = app_commands.Group(name="media", description="Manage media processing.")
    media_config_group = app_commands.Group(name="config", description="Configure media processing settings.", parent=media_group)
    channel_group = app_commands.Group(name="channel", description="Manage channel-specific settings.")
    context_group = app_commands.Group(name="context", description="Manage and inspect LLM context.")
    model_group = app_commands.Group(name="model", description="Inspect model capabilities.")
    user_group = app_commands.Group(name="user", description="Manage user profile settings and statistics.")

    @llm_group.command(name="settings", description="Configure core LLM parameters for the server.")
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

        setting_path = [part.strip() for part in setting.split(".") if part.strip()]
        if not setting_path:
            await interaction.followup.send("Setting name cannot be empty.", ephemeral=True)
            return

        target = settings[guild_id]["media"][media_type]
        for path_part in setting_path[:-1]:
            if not isinstance(target.get(path_part), dict):
                target[path_part] = {}
            target = target[path_part]

        target[setting_path[-1]] = final_value
        
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
                value_str = "\n".join([f"**{key}:** `{value}`" for key, value in self._flatten_config(config)])
                if not value_str:
                    value_str = "No settings configured."
                embed.add_field(name=media_type.replace("_", " ").title(), value=value_str, inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    def _flatten_config(self, config: dict, prefix: str = ""):
        if not isinstance(config, dict):
            return []

        rows = []
        for key, value in config.items():
            full_key = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                rows.extend(self._flatten_config(value, full_key))
            else:
                rows.append((full_key, value))
        return rows

    def _coerce_bool(self, value, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y", "on"}:
                return True
            if normalized in {"false", "0", "no", "n", "off"}:
                return False
        if value is None:
            return default
        return bool(value)

    @channel_group.command(name="override", description="Override global settings for a specific channel.")
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

    @llm_group.command(name="blacklist", description="Block or unblock LLM-generated bot output in a channel.")
    @app_commands.describe(
        channel="Channel to configure. Defaults to the current channel.",
        blacklisted="True blocks LLM output; false unblocks it. Leave empty to view status."
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def llm_blacklist(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        blacklisted: Optional[bool] = None
    ):
        await interaction.response.defer(ephemeral=True)

        target_channel = channel or interaction.channel
        if not isinstance(target_channel, (discord.TextChannel, discord.Thread)):
            await interaction.followup.send("This command can only target text channels or threads.", ephemeral=True)
            return

        guild_id = str(interaction.guild.id)
        channel_id = str(target_channel.id)
        settings = await self.bot.store.get_settings()
        guild_settings = settings.setdefault(guild_id, {})
        blocked_channels = guild_settings.get("llm_blacklisted_channels", [])
        if isinstance(blocked_channels, (str, int)):
            blocked_channels = [blocked_channels]
        blocked_channel_ids = {str(value) for value in blocked_channels or []}

        if blacklisted is None:
            is_blocked = await self.bot.context_manager.is_channel_llm_blacklisted(guild_id, channel_id)
            status = "blacklisted" if is_blocked else "not blacklisted"
            channel_list = ", ".join(f"<#{cid}>" for cid in sorted(blocked_channel_ids)) if blocked_channel_ids else "None"
            await interaction.followup.send(
                f"{target_channel.mention} is {status} for LLM output.\nBlacklisted channels: {channel_list}",
                ephemeral=True
            )
            return

        if blacklisted:
            blocked_channel_ids.add(channel_id)
        else:
            blocked_channel_ids.discard(channel_id)

        guild_settings["llm_blacklisted_channels"] = sorted(blocked_channel_ids)
        await self.bot.store.save_settings(settings)

        action = "blacklisted" if blacklisted else "unblacklisted"
        await interaction.followup.send(
            f"{target_channel.mention} is now {action} for LLM-generated output.",
            ephemeral=True
        )

    @llm_group.command(name="decision", description="View or toggle the ambient decision model.")
    @app_commands.describe(
        enabled="True enables ambient decisions; false only replies to direct mentions/replies. Leave empty to view status.",
        channel="Optional channel override. Leave empty to update the server default."
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def llm_decision(
        self,
        interaction: discord.Interaction,
        enabled: Optional[bool] = None,
        channel: Optional[discord.TextChannel] = None
    ):
        await interaction.response.defer(ephemeral=True)

        guild_id = str(interaction.guild.id)

        if enabled is None:
            target_channel = channel or interaction.channel
            if not isinstance(target_channel, (discord.TextChannel, discord.Thread)):
                await interaction.followup.send("This command can only inspect text channels or threads.", ephemeral=True)
                return

            current_settings = await self.bot.context_manager.get_guild_and_channel_settings(guild_id, str(target_channel.id))
            default_enabled = getattr(self.bot.config, "DECISION_LLM_ENABLED", True)
            effective_enabled = self._coerce_bool(
                current_settings.get("decision_llm_enabled"),
                default=default_enabled,
            )

            guild_settings = await self.bot.store.get_guild_settings(guild_id)
            server_value = guild_settings.get("decision_llm_enabled", default_enabled)
            channel_value = (
                guild_settings
                .get("channel_overrides", {})
                .get(str(target_channel.id), {})
                .get("decision_llm_enabled")
            )

            channel_line = "unset" if channel_value is None else str(self._coerce_bool(channel_value, default=default_enabled))
            await interaction.followup.send(
                "\n".join([
                    f"Decision model effective for {target_channel.mention}: {effective_enabled}",
                    f"Server default: {self._coerce_bool(server_value, default=default_enabled)}",
                    f"Channel override: {channel_line}",
                ]),
                ephemeral=True,
            )
            return

        settings = await self.bot.store.get_settings()
        guild_settings = settings.setdefault(guild_id, {})

        if channel:
            channel_overrides = guild_settings.setdefault("channel_overrides", {})
            channel_settings = channel_overrides.setdefault(str(channel.id), {})
            channel_settings["decision_llm_enabled"] = bool(enabled)
            scope_text = channel.mention
        else:
            guild_settings["decision_llm_enabled"] = bool(enabled)
            scope_text = "this server"

        await self.bot.store.save_settings(settings)

        mode_text = (
            "enabled. The bot may use the decision model for ambient replies/reactions."
            if enabled
            else "disabled. The bot will only answer direct mentions/replies."
        )
        await interaction.followup.send(
            f"Decision model is now {mode_text} Scope: {scope_text}",
            ephemeral=True,
        )

    @context_group.command(name="reset", description="Reset the bot's context for a channel or the entire server.")
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

    @model_group.command(name="info", description="Check capabilities of a specific model.")
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
            metadata = {
                "Last Updated": timestamp,
                "Messages Since Update": str(summary_data["messages_since_summary"]),
                "Messages Processed": str(summary_data["messages_processed"]),
                "Summary Type": summary_data["summary_type"].title()
            }
            
            view = AdvancedPaginationView(
                content=summary_text,
                title=f"Channel Summary: #{target_channel.name}",
                color=discord.Color.blue(),
                metadata=metadata
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

    @user_group.command(name="profile", description="Manage user profile settings and statistics.")
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

    @context_group.command(name="show", description="Show the complete context that would be sent to the LLM.")
    @app_commands.checks.has_permissions(administrator=True)
    async def show_context(self, interaction: discord.Interaction, 
                          message_id: str = None,
                          raw_format: bool = False,
                          show_gif_frames: bool = False,
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
        - show_gif_frames: Display actual GIF frames as images (may send multiple messages)
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
                context_text = json.dumps(context, indent=2, ensure_ascii=False)
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
                    
                    # Handle content that can be either string or list of content parts
                    content_text = ""
                    if isinstance(content, list):
                        # Handle structured content (e.g., text + images from GIF extraction)
                        for part in content:
                            if isinstance(part, dict):
                                if part.get("type") == "text":
                                    content_text += part.get("text", "") + "\n"
                                elif part.get("type") == "image_url":
                                    image_url = part.get("image_url", {}).get("url", "")
                                    if image_url.startswith("data:image/"):
                                        # Base64 image data - show as placeholder
                                        content_text += f"🖼️ [Image: {image_url[:50]}...] (Base64 encoded)\n"
                                    else:
                                        # Regular URL - show the URL
                                        content_text += f"🖼️ [Image: {image_url}]\n"
                                else:
                                    # Other content types
                                    content_text += f"📎 [Content: {part.get('type', 'unknown')}]\n"
                            else:
                                content_text += str(part) + "\n"
                    else:
                        # Regular string content
                        content_text = str(content)
                    
                    # Add content with some formatting
                    if len(content_text) > 500:
                        context_text += f"{content_text[:500]}...\n\n"
                    else:
                        context_text += f"{content_text}\n\n"

            common_fields = [
                {
                    "name": "Source Message",
                    "value": f"ID: {target_message.id}\nAuthor: {target_message.author.display_name}\nChannel: #{target_message.channel.name}",
                    "inline": False
                },
                {
                    "name": "Context Components",
                    "value": (
                        f"Bot Identity: {'✅' if include_bot_identity else '❌'}\n"
                        f"Channel Summary: {'✅' if include_channel_summary else '❌'}\n"
                        f"User Profiles: {'✅' if include_user_profiles else '❌'}\n"
                        f"Conversation History: {'✅' if include_conversation_history else '❌'}\n"
                        f"Reply Chain: {'✅' if include_reply_chain else '❌'}\n"
                        f"Current Message: {'✅' if include_current_message else '❌'}"
                    ),
                    "inline": False
                },
                {
                    "name": "Settings Used",
                    "value": (
                        f"Model: {settings.get('model', 'N/A')}\n"
                        f"Messages: {len(context)} total\n"
                        f"Context Length: {len(context_text)} chars"
                    ),
                    "inline": False
                }
            ]

            if raw_format:
                raw_pages = MessageChunker.split_content(context_text, max_length=1800)
                page_descriptions = [f"```json\n{page}\n```" for page in raw_pages]
            else:
                page_descriptions = MessageChunker.split_content(context_text, max_length=1800)

            if not page_descriptions:
                page_descriptions = ["No context generated."]

            pages = [{"description": page, "fields": common_fields} for page in page_descriptions]
            view = AdvancedPaginationView(
                content=pages,
                title="LLM Context Debug",
                color=discord.Color.purple()
            )

            embed = view.create_embed()
            if len(pages) > 1:
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            else:
                await interaction.followup.send(embed=embed, ephemeral=True)
            
            # If show_gif_frames is enabled, extract and display GIF frames separately
            if show_gif_frames:
                gif_frames_found = 0
                for item in context:
                    content = item.get("content", "")
                    role = item.get("role", "unknown")
                    
                    if isinstance(content, list):
                        # Look for image content in structured content
                        text_parts = []
                        image_parts = []
                        
                        for part in content:
                            if isinstance(part, dict):
                                if part.get("type") == "text":
                                    text_parts.append(part.get("text", ""))
                                elif part.get("type") == "image_url":
                                    image_url = part.get("image_url", {}).get("url", "")
                                    if image_url.startswith("data:image/"):
                                        image_parts.append(image_url)
                        
                        # If we found images in this content item, display them
                        if image_parts:
                            gif_frames_found += len(image_parts)
                            
                            # Create an embed for this set of frames
                            frame_embed = discord.Embed(
                                title=f"🎬 GIF Frames from {role.upper()} Message",
                                description="\n".join(text_parts) if text_parts else "Extracted frames from animated GIF",
                                color=discord.Color.green()
                            )
                            frame_embed.add_field(
                                name="Frame Count", 
                                value=f"{len(image_parts)} frames extracted",
                                inline=True
                            )
                            
                            await interaction.followup.send(embed=frame_embed, ephemeral=True)
                            
                            # Send each frame as a separate message due to Discord limitations
                            for i, frame_data in enumerate(image_parts[:5]):  # Limit to 5 frames to avoid spam
                                try:
                                    # Convert base64 to bytes for Discord file upload
                                    import base64
                                    import io
                                    
                                    # Extract the base64 data (remove data:image/jpeg;base64, prefix)
                                    if "," in frame_data:
                                        base64_data = frame_data.split(",", 1)[1]
                                        image_bytes = base64.b64decode(base64_data)
                                        
                                        # Create a file-like object
                                        image_file = discord.File(
                                            io.BytesIO(image_bytes), 
                                            filename=f"gif_frame_{i+1}.jpg"
                                        )
                                        
                                        frame_embed = discord.Embed(
                                            title=f"Frame {i+1}/{len(image_parts)}",
                                            color=discord.Color.blue()
                                        )
                                        frame_embed.set_image(url=f"attachment://gif_frame_{i+1}.jpg")
                                        
                                        await interaction.followup.send(
                                            embed=frame_embed, 
                                            file=image_file, 
                                            ephemeral=True
                                        )
                                        
                                except Exception as frame_error:
                                    logging.warning(f"Could not display frame {i+1}: {frame_error}")
                                    await interaction.followup.send(
                                        f"❌ Could not display frame {i+1}: {str(frame_error)[:100]}", 
                                        ephemeral=True
                                    )
                            
                            if len(image_parts) > 5:
                                await interaction.followup.send(
                                    f"ℹ️ Showing first 5 frames only. {len(image_parts) - 5} additional frames not displayed to avoid spam.",
                                    ephemeral=True
                                )
                
                if gif_frames_found == 0:
                    await interaction.followup.send(
                        "ℹ️ No GIF frames found in context. Try processing a message with an animated GIF first.",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        f"✅ Displayed {min(gif_frames_found, 5)} GIF frames from context.",
                        ephemeral=True
                    )
            
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
            
            title = f"🔄 Reload Results ({success_count}/{total_count} successful)"
            color = discord.Color.green() if success_count == total_count else discord.Color.orange()
            embed = discord.Embed(title=title, color=color)
            
            # Group results by type
            cog_results = [r for r in results if r["type"] == "cog"]
            config_results = [r for r in results if r["type"] == "config"]
            prompt_results = [r for r in results if r["type"] == "prompt"]
            
            sections = []
            needs_pagination = False

            if cog_results:
                cog_text = []
                for result in cog_results:
                    status = "✅" if result["success"] else "❌"
                    cog_text.append(f"{status} {result['name']}")
                    if not result["success"]:
                        cog_text.append(f"   └─ {result['error']}")
                cog_value = "\n".join(cog_text) if cog_text else "None"
                sections.append((
                    f"Cogs ({sum(1 for r in cog_results if r['success'])}/{len(cog_results)})",
                    cog_value
                ))
                if len(cog_value) > 1000:
                    needs_pagination = True

            if config_results:
                config_text = []
                for result in config_results:
                    status = "✅" if result["success"] else "❌"
                    config_text.append(f"{status} {result['name']}")
                    if not result["success"]:
                        config_text.append(f"   └─ {result['error']}")
                config_value = "\n".join(config_text)
                sections.append(("Configuration", config_value))
                if len(config_value) > 1000:
                    needs_pagination = True

            if prompt_results:
                prompt_text = []
                for result in prompt_results:
                    status = "✅" if result["success"] else "❌"
                    prompt_text.append(f"{status} {result['name']}")
                    if not result["success"]:
                        prompt_text.append(f"   └─ {result['error']}")
                prompt_value = "\n".join(prompt_text)
                sections.append(("Prompts", prompt_value))
                if len(prompt_value) > 1000:
                    needs_pagination = True

            result_text = "\n\n".join(
                f"**{section_title}**\n{section_value}" for section_title, section_value in sections
            )
            if len(result_text) > 1800:
                needs_pagination = True

            if needs_pagination:
                await AdvancedPaginationView.send_paginated_text(
                    interaction=interaction,
                    content=result_text or "No reload results available.",
                    title=title,
                    color=color,
                    ephemeral=True
                )
                return

            for section_title, section_value in sections:
                embed.add_field(name=section_title, value=section_value or "None", inline=False)

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
            
            # Update live component references with a fresh Config instance.
            new_config = config.Config()
            self.bot.config = new_config
            self.bot.llm_provider.config = new_config
            self.bot.context_manager.config = new_config
            
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
                        self.bot.config.BEHAVIOR_PROMPT = f.read().strip()
                    self.bot.context_manager.config = self.bot.config
                    
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
                        self.bot.config.CAPABILITIES_PROMPT = f.read().strip()
                    self.bot.context_manager.config = self.bot.config
                    
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
            
            # Re-exec the current process instead of spawning an untracked child process.
            python_executable = os.path.abspath(sys.executable)
            script_path = os.path.abspath(sys.argv[0])
            if not os.path.isfile(python_executable):
                raise RuntimeError(f"Python executable not found: {python_executable}")
            if not os.path.isfile(script_path):
                raise RuntimeError(f"Bot entrypoint not found: {script_path}")

            restart_args = [python_executable, script_path, *sys.argv[1:]]
            logging.info("Restarting bot using executable=%s entrypoint=%s", python_executable, script_path)
            
            # Close the current bot instance
            logging.info("Shutting down current bot instance...")
            await self.bot.close()
            # Restart args are built from the validated current executable and entrypoint.
            os.execv(python_executable, restart_args)  # nosec B606
            
        except Exception as e:
            logging.error(f"Error during restart: {e}")
            try:
                await interaction.edit_original_response(content=f"❌ **Failed to restart bot:** {str(e)}")
            except discord.HTTPException as edit_error:
                # If we can't edit the response, the bot might already be shutting down
                logging.debug("Could not edit restart failure response: %s", edit_error)

async def setup(bot):
    await bot.add_cog(AdminCommands(bot))
