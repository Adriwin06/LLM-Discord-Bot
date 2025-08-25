# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/cogs/profile_commands.py
import discord
from discord.ext import commands
from discord import app_commands
import logging

class ProfileCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    note_group = app_commands.Group(name="note", description="Commands for managing user profiles.")

    @note_group.command(name="add", description="Adds a permanent manual note to a user.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def add_note(self, interaction: discord.Interaction, user: discord.Member, note: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild.id)
        user_id = str(user.id)

        data = await self.bot.store.get_data()
        if guild_id not in data:
            data[guild_id] = {"users": {}}
        if "users" not in data[guild_id]:
            data[guild_id]["users"] = {}
        if user_id not in data[guild_id]["users"]:
            data[guild_id]["users"][user_id] = {}
            
        data[guild_id]["users"][user_id]["manual_note"] = note
        
        await self.bot.store.save_data(data)
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
        
        manual_note = user_data.get("manual_note", "N/A")
        ai_summary = user_data.get("ai_summary", "N/A")

        embed.add_field(name="Manual Note", value=manual_note, inline=False)
        embed.add_field(name="AI-Generated Summary", value=ai_summary, inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    @note_group.command(name="refresh-ai", description="Forces an update of the AI-generated summary for a user.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def refresh_ai_note(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)
        
        # This is a placeholder for the actual logic which would be complex.
        # It needs to gather user's recent messages and call the context_manager/llm_provider.
        logging.info(f"AI summary refresh triggered for {user.display_name} by {interaction.user}")
        
        # Placeholder implementation
        await self.bot.context_manager.update_user_profile(interaction.guild.id, user.id, []) # Pass empty messages for now
        
        await interaction.followup.send(f"AI summary refresh for {user.display_name} has been queued.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(ProfileCommands(bot))
async def setup(bot):
    await bot.add_cog(ProfileCommands(bot))
