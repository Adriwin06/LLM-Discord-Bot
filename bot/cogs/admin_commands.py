# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/cogs/admin_commands.py
import discord
from discord.ext import commands
from discord import app_commands
import logging

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
        
        data = await self.bot.store.get_data()
        
        if target.lower() == "channel":
            channel_id = str(interaction.channel.id)
            if guild_id in data and "channels" in data[guild_id] and channel_id in data[guild_id]["channels"]:
                del data[guild_id]["channels"][channel_id]["summary"]
                data[guild_id]["channels"][channel_id]["messages_since_summary"] = 0
                await self.bot.store.save_data(data)
                await interaction.followup.send(f"Context for this channel has been reset.", ephemeral=True)
            else:
                await interaction.followup.send(f"No context to reset for this channel.", ephemeral=True)
        elif target.lower() == "guild":
            if guild_id in data and "channels" in data[guild_id]:
                for channel_data in data[guild_id]["channels"].values():
                    if "summary" in channel_data:
                        del channel_data["summary"]
                    channel_data["messages_since_summary"] = 0
                await self.bot.store.save_data(data)
                await interaction.followup.send(f"Context for the entire server has been reset.", ephemeral=True)
            else:
                await interaction.followup.send(f"No context to reset for this server.", ephemeral=True)
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

async def setup(bot):
    await bot.add_cog(AdminCommands(bot))
