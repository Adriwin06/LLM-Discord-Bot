# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/cogs/levelup_commands.py
import discord
from discord.ext import commands
from discord import app_commands
import json
import math
import logging
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple, Callable
import aiofiles
import os
import asyncio
from .utilities import UtilityHelpers, AdvancedPaginationView

# Interactive View for the Leaderboard
class LeaderboardView(discord.ui.View):
    def __init__(self, *, 
                 interaction: discord.Interaction, 
                 user_rankings: List[Tuple[str, Dict[str, Any]]], 
                 total_server_xp: float, 
                 author_rank: str, 
                 total_users: int, 
                 level_calculator: Callable[[float], int],
                 items_per_page: int = 10):
        
        super().__init__(timeout=None)  # Disable default timeout to use custom inactivity timer
        self.interaction = interaction
        self.user_rankings = user_rankings
        self.total_server_xp = total_server_xp
        self.author_rank = author_rank
        self.total_users = total_users
        self.level_calculator = level_calculator
        self.items_per_page = items_per_page
        
        self.current_page = 1
        self.total_pages = max(1, math.ceil(len(self.user_rankings) / self.items_per_page))
        self.message = None

        self.timeout_task = None  # Add to track the inactivity timer task

    def format_xp(self, xp: float) -> str:
        """Formats XP to be compact (e.g., 2.8K)."""
        if xp >= 1000:
            return f"{xp/1000:.1f}K"
        return f"{int(xp)}"

    async def create_embed(self) -> discord.Embed:
        """Creates the leaderboard embed for the current page."""
        self.update_button_states()
        
        embed = discord.Embed(
            title="LevelUp Exp Leaderboard",
            color=discord.Color.yellow()
        )
        embed.add_field(name="Total Experience", value=f"{int(self.total_server_xp):,} 💡")

        start_index = (self.current_page - 1) * self.items_per_page
        end_index = start_index + self.items_per_page
        
        leaderboard_slice = self.user_rankings[start_index:end_index]
        
        description = []
        for i, (user_id, data) in enumerate(leaderboard_slice, start=start_index + 1):
            try:
                user = self.interaction.guild.get_member(int(user_id))
                if user is None:
                    continue  # Skip unknown users
                name = UtilityHelpers.safe_username(user)
                total_xp = data.get("xp", 0.0)
                level = self.level_calculator(total_xp)
                
                # Format: 1. Adriwin (2.8K 🎖5)
                description.append(
                    f"`{i}.` **{name}** ({self.format_xp(total_xp)} 🎖{level})"
                )
            except (ValueError, AttributeError):
                continue
        
        embed.description = "\n".join(description) if description else "No users on this page."
        embed.set_footer(text=f"Page {self.current_page}/{self.total_pages} | You: {self.author_rank}/{self.total_users}")
        
        return embed

    def update_button_states(self):
        """Disables/enables navigation buttons based on the current page."""
        self.children[0].disabled = self.current_page == 1
        self.children[2].disabled = self.current_page == self.total_pages

    @discord.ui.button(style=discord.ButtonStyle.primary, emoji="⬅️")
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 1:
            self.current_page -= 1
            embed = await self.create_embed()
            await interaction.response.edit_message(embed=embed, view=self)
            self._reset_inactivity_timer()  # Reset timer after interaction

    @discord.ui.button(style=discord.ButtonStyle.danger, emoji="✖️")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Cancel the inactivity timer if running
        if self.timeout_task and not self.timeout_task.done():
            self.timeout_task.cancel()
        # Delete the entire message (embed and buttons)
        try:
            await interaction.message.delete()
        except discord.NotFound:
            pass  # Message was already deleted
        self.stop()

    @discord.ui.button(style=discord.ButtonStyle.primary, emoji="➡️")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.total_pages:
            self.current_page += 1
            embed = await self.create_embed()
            await interaction.response.edit_message(embed=embed, view=self)
            self._reset_inactivity_timer()  # Reset timer after interaction
            
    def _reset_inactivity_timer(self):
        """Cancel existing timer and start a new 30-second inactivity timer."""
        if self.timeout_task and not self.timeout_task.done():
            self.timeout_task.cancel()
        self.timeout_task = asyncio.create_task(self._timeout_after_inactivity())

    async def _timeout_after_inactivity(self):
        """Wait 30 seconds and remove buttons if no interaction."""
        await asyncio.sleep(30)
        try:
            await self.message.edit(view=None)
        except discord.NotFound:
            pass  # Message was deleted
        self.stop()

class LevelUpCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.levels_file = "data/levels.json"
        self.levels_data = {}
        self._levels_lock = asyncio.Lock()
        self._pending_levels_save_task: Optional[asyncio.Task] = None
        self._levels_save_requested = False
        self.xp_per_message = [15, 25]
        self.cooldown_seconds = 60
        self.last_message = {}
        self.voice_tracking = {}
        self.voice_xp_per_minute = 1.0
        
    async def cog_load(self):
        await self.load_levels_data()
        await self.initialize_voice_tracking()
        
    async def load_levels_data(self):
        """Load the levels.json file with UTF-8 encoding"""
        try:
            async with self._levels_lock:
                if os.path.exists(self.levels_file):
                    async with aiofiles.open(self.levels_file, 'r', encoding='utf-8') as f:
                        content = await f.read()
                        self.levels_data = json.loads(content)
                        logging.info("Loaded levels data successfully")
                else:
                    self.levels_data = {"configs": {}}
                    await self._write_levels_data_unlocked(self.levels_data)
                    logging.info("Created new levels data file")
        except Exception as e:
            logging.error(f"Error loading levels data: {e}")
            self.levels_data = {"configs": {}}
    
    async def initialize_voice_tracking(self):
        """Initialize voice tracking for users already in voice channels"""
        try:
            for guild in self.bot.guilds:
                guild_id = str(guild.id)
                config = self.get_guild_config(guild_id)
                if not config.get("enabled", True):
                    continue
                self.voice_tracking[guild_id] = {}
                for member in guild.members:
                    if member.voice and member.voice.channel and not member.bot:
                        if self.can_gain_voice_xp(member, config):
                            self.voice_tracking[guild_id][str(member.id)] = datetime.now(timezone.utc)
            logging.info("Initialized voice tracking")
        except Exception as e:
            logging.error(f"Error initializing voice tracking: {e}")
    
    def can_gain_voice_xp(self, member, config):
        """Check if a member can gain voice XP based on their state and settings"""
        if not member.voice or not member.voice.channel: return False
        if member.voice.channel.id in config.get("ignoredchannels", []): return False
        if member.id in config.get("ignoredusers", []): return False
        voice_settings = config.get("voice_settings", {})
        if voice_settings.get("ignore_muted", False) and (member.voice.mute or member.voice.self_mute): return False
        if voice_settings.get("ignore_deafened", False) and (member.voice.deaf or member.voice.self_deaf): return False
        if voice_settings.get("ignore_solo", False):
            human_members = [m for m in member.voice.channel.members if not m.bot]
            if len(human_members) <= 1: return False
        return True
    
    async def save_levels_data(self):
        """Save the levels.json file with UTF-8 encoding using an atomic replace."""
        try:
            async with self._levels_lock:
                snapshot = json.loads(json.dumps(self.levels_data))
                await self._write_levels_data_unlocked(snapshot)
        except Exception as e:
            logging.error(f"Error saving levels data: {e}")

    async def _write_levels_data_unlocked(self, data: Dict[str, Any]):
        directory = os.path.dirname(self.levels_file)
        os.makedirs(directory, exist_ok=True)

        temp_path = f"{self.levels_file}.tmp"
        try:
            async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(data, indent=4))
            os.replace(temp_path, self.levels_file)
        except Exception:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                logging.warning("Could not remove temporary levels data file: %s", temp_path)
            raise

    def _schedule_levels_save(self):
        self._levels_save_requested = True
        if self._pending_levels_save_task and not self._pending_levels_save_task.done():
            return

        try:
            self._pending_levels_save_task = asyncio.get_running_loop().create_task(self._run_scheduled_levels_save())
            self._pending_levels_save_task.add_done_callback(self._log_scheduled_save_error)
        except RuntimeError:
            pass

    async def _run_scheduled_levels_save(self):
        while self._levels_save_requested:
            self._levels_save_requested = False
            await self.save_levels_data()

    def _log_scheduled_save_error(self, task: asyncio.Task):
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logging.error("Scheduled levels data save failed: %s", e)
    
    def get_guild_config(self, guild_id: str) -> Dict[str, Any]:
        """Get or create guild config, ensuring compatibility"""
        if guild_id not in self.levels_data["configs"]:
            self.levels_data["configs"][guild_id] = {
                "users": {}, "enabled": True, "levelroles": {}, "autoremove": True,
                "cooldown": 30, "min_length": 4, "rolebonus": {"msg": {}, "voice": {}},
                "ignoredchannels": [], "ignoredusers": [], "prestigelevel": 8, "prestigedata": {},
                "voice_settings": {"xp_per_minute": 2.0, "ignore_muted": True, "ignore_deafened": True, "ignore_solo": True},
                "notify": True, "notifylog": None,
                "message_xp": {"min": 3, "max": 6, "per_char": 0.0},
            }
            # Asynchronously save new config without blocking
            self._schedule_levels_save()
        return self.levels_data["configs"][guild_id]

    def get_user_data(self, guild_id: str, user_id: str) -> Dict[str, Any]:
        """Get or create user data"""
        config = self.get_guild_config(guild_id)
        if user_id not in config["users"]:
            config["users"][user_id] = {
                "xp": 0.0, "voice": 0.0, "messages": 0, "level": 0,
                "last_active": datetime.now(timezone.utc).isoformat()
            }
            self._schedule_levels_save()
        return config["users"][user_id]

    def _get_role_by_id(self, guild: discord.Guild, role_id) -> Optional[discord.Role]:
        """Return a Discord role from JSON-stored IDs that may be int or str."""
        try:
            return guild.get_role(int(role_id))
        except (TypeError, ValueError):
            return None
    
    def calculate_level_from_xp(self, xp: float) -> int:
        """Calculate level from XP using the standard formula"""
        if xp < 0:
            return 0
        return int(math.sqrt(xp / 100))
    
    def calculate_xp_for_level(self, level: int) -> int:
        """Calculate XP needed to reach a specific level"""
        return level * level * 100
    
    def calculate_xp_for_next_level(self, current_xp: float) -> tuple:
        """Calculate XP needed for next level and progress"""
        current_level = self.calculate_level_from_xp(current_xp)
        next_level = current_level + 1
        current_level_xp = self.calculate_xp_for_level(current_level)
        next_level_xp = self.calculate_xp_for_level(next_level)
        progress = current_xp - current_level_xp
        needed = next_level_xp - current_level_xp
        return next_level_xp - current_xp, progress, needed, next_level
    
    # Create a group for level commands
    level_group = app_commands.Group(name="level", description="Leveling system commands")
    level_prestige_group = app_commands.Group(name="prestige", description="Manage prestige settings", parent=level_group)
    level_voice_group = app_commands.Group(name="voice", description="Manage voice XP settings", parent=level_group)
    
    # --- MAJOR REFACTOR: level_profile ---
    @level_group.command(name="profile", description="View your or someone else's level profile")
    async def level_profile(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        target_user = user or interaction.user
        guild_id = str(interaction.guild.id)
        config = self.get_guild_config(guild_id)
        if not config.get("enabled", True):
            await interaction.response.send_message("❌ Leveling is disabled in this server.", ephemeral=True)
            return

        user_data = self.get_user_data(guild_id, str(target_user.id))
        
        # CORRECTED: 'xp' is the total XP. 'voice' is time in seconds.
        total_xp = user_data.get("xp", 0.0)
        voice_seconds = user_data.get("voice", 0.0)
        messages = user_data.get("messages", 0)
        stars = user_data.get("stars", 0) # Handle the 'stars' field
        
        current_level = self.calculate_level_from_xp(total_xp)
        if user_data.get("level") != current_level:
            user_data["level"] = current_level
            await self.save_levels_data()

        _, progress, needed, _ = self.calculate_xp_for_next_level(total_xp)

        # Format voice time using utility helper
        voice_time_str = f"{UtilityHelpers.format_time_duration(int(voice_seconds))} in voice"

        # Calculate server rank and total server XP
        all_users_data = config.get("users", {})
        sorted_users = sorted(all_users_data.items(), key=lambda item: item[1].get("xp", 0.0), reverse=True)
        total_server_xp = sum(u.get("xp", 0.0) for u in all_users_data.values())
        
        rank = -1
        for i, (uid, _) in enumerate(sorted_users):
            if uid == str(target_user.id):
                rank = i + 1
                break
        
        user_xp_percentage = (total_xp / total_server_xp * 100) if total_server_xp > 0 else 0

        # Build the embed to match the old bot's style
        embed = discord.Embed(
            title=f"{UtilityHelpers.safe_username(target_user)}'s Profile",
            color=0xf1c40f # Yellow color to match
        )
        embed.set_thumbnail(url=target_user.display_avatar.url)

        # Build description string
        description = (
            f"🏅 | Level {current_level}\n"
            f"⭐ | {stars} stars\n"
            f"💬 | {messages:,} messages sent\n"
            f"🎙️ | {voice_time_str}\n"
            f"💡 | {progress:,.0f}/{needed:,.0f} Exp ({total_xp:,.0f} total)"
        )
        embed.description = description
        
        # Progress Bar
        progress_percentage = (progress / needed) * 100 if needed > 0 else 100
        progress_bar_length = 20
        filled_length = int(progress_bar_length * progress_percentage / 100)
        progress_bar = "█" * filled_length + " " * (progress_bar_length - filled_length)
        
        embed.add_field(
            name="Progress",
            value=f"`{progress_bar}` **{progress_percentage:.1f}%**",
            inline=False
        )

        # Rank footer
        if rank != -1:
            embed.set_footer(text=f"Rank {rank}, with {user_xp_percentage:.1f}% of the total server Exp")
        
        await interaction.response.send_message(embed=embed)

    # --- REWORKED: level_leaderboard ---
    @level_group.command(name="leaderboard", description="View the server leaderboard")
    async def level_leaderboard(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        config = self.get_guild_config(guild_id)
        if not config.get("enabled", True):
            await interaction.response.send_message("❌ Leveling is disabled in this server.", ephemeral=True)
            return

        users_data = config.get("users", {})
        if not users_data:
            await interaction.response.send_message("📊 No leveling data found for this server yet!", ephemeral=True)
            return

        # Sort all users with XP > 0 and are currently in the guild
        user_rankings = []
        for uid, data in users_data.items():
            if data.get("xp", 0.0) > 0:
                try:
                    member = interaction.guild.get_member(int(uid))
                    if member is not None:
                        user_rankings.append((uid, data))
                except ValueError:
                    continue

        user_rankings.sort(key=lambda item: item[1].get("xp", 0.0), reverse=True)

        if not user_rankings:
            await interaction.response.send_message("📊 No users with XP found on the leaderboard.", ephemeral=True)
            return

        # Calculate total server XP from the ranked users
        total_server_xp = sum(data.get("xp", 0.0) for _, data in user_rankings)

        # Find the rank of the user who initiated the command
        author_rank = "N/A"
        for i, (user_id, _) in enumerate(user_rankings):
            if user_id == str(interaction.user.id):
                author_rank = f"{i + 1}"
                break
        
        total_users = len(user_rankings)

        # Create and send the interactive view
        view = LeaderboardView(
            interaction=interaction,
            user_rankings=user_rankings,
            total_server_xp=total_server_xp,
            author_rank=author_rank,
            total_users=total_users,
            level_calculator=self.calculate_level_from_xp
        )
        
        initial_embed = await view.create_embed()
        await interaction.response.send_message(embed=initial_embed, view=view)
        view.message = await interaction.original_response()
        view._reset_inactivity_timer()  # Start the initial 30-second timer

    @level_group.command(name="settings", description="View or configure leveling settings (Admin only)")
    @app_commands.describe(
        enabled="Enable or disable leveling in this server",
        cooldown="Cooldown between XP gains (in seconds)",
        min_length="Minimum message length to earn XP",
        notify="Enable level up notifications"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def level_settings(
        self,
        interaction: discord.Interaction,
        enabled: Optional[bool] = None,
        cooldown: Optional[int] = None,
        min_length: Optional[int] = None,
        notify: Optional[bool] = None
    ):
        guild_id = str(interaction.guild.id)
        config = self.get_guild_config(guild_id)

        def fmt_bool(v): return "Yes" if v else "No" # CHANGED: To display Yes/No
        def fmt_channel(cid):
            if not cid: return "None"
            ch = interaction.guild.get_channel(cid)
            return ch.mention if ch else f"(deleted #{cid})"

        if all(param is None for param in [enabled, cooldown, min_length, notify]):
            # --- START OF MAJOR REFACTOR ---
            # This entire block has been rewritten to exactly match the desired screenshot output.
            voice = config.get("voice_settings", {})
            msgxp = config.get("message_xp", {})
            rolebonus = config.get("rolebonus", {"msg": {}, "voice": {}})
            prestigedata = config.get("prestigedata", {})
            
            sections = []
            needs_pagination = False

            main_value = (
                f"System Enabled: {fmt_bool(config.get('enabled', True))}\n"
                f"Profile Type: Embeds\n"
                f"Style Override: None\n"
                f"Include Balance: No"
            )
            sections.append(("Main", main_value))

            messages_value = (
                f"Message XP: {msgxp.get('min', 3)} - {msgxp.get('max', 6)}\n"
                f"Min Msg Length: {config.get('min_length', 4)}\n"
                f"Cooldown: {config.get('cooldown', 60)} seconds\n"
                f"Command XP: False"
            )
            sections.append(("Messages", messages_value))

            voice_value = (
                f"Voice XP: {voice.get('xp_per_minute', 1.0)} per minute\n"
                f"Ignore Muted: {fmt_bool(voice.get('ignore_muted', False))}\n"
                f"Ignore Solo: {fmt_bool(voice.get('ignore_solo', False))}\n"
                f"Ignore Deafened: {fmt_bool(voice.get('ignore_deafened', False))}\n"
                f"Ignore Invisible: True"
            )
            sections.append(("Voice", voice_value))

            algorithm_value = (
                "Base Multiplier: 100\n"
                "Exp Multiplier: 2.0\n"
                "Equation: 100 x (level ^ 2.0) = XP"
            )
            sections.append(("Level Algorithm", algorithm_value))

            levelups_value = (
                f"Notify In channel: {fmt_bool(config.get('notify', True))}\n"
                f"• Send levelup message in the channel the user is typing in\n"
                f"Notify in DMs: False\n"
                f"• Log channel for levelup messages\n"
                f"Notify Channel: {fmt_channel(config.get('notifylog'))}\n"
                f"Mention User: False\n"
                f"AutoRemove Roles: {fmt_bool(config.get('autoremove', True))}"
            )
            sections.append(("LevelUps", levelups_value))

            levelroles = config.get("levelroles", {})
            if levelroles:
                lr_lines = ["➤ Level roles will Stack"]
                # Sort by level descending to match screenshot
                for lvl, rid in sorted(levelroles.items(), key=lambda x: int(x[0]), reverse=True):
                    r = self._get_role_by_id(interaction.guild, rid)
                    lr_lines.append(f"• Level {lvl}: {r.mention if r else f'(deleted role {rid})'}")
                levelroles_value = "\n".join(lr_lines)
                sections.append(("Level Roles", levelroles_value))
                if len(levelroles_value) > 1024:
                    needs_pagination = True

            if prestigedata:
                pr_lines = [
                    "➤ Prestige roles will Stack",
                    f"➤ Requires reaching level {config.get('prestigelevel', 8)} to activate",
                    "➤ Level roles will be reset after prestiging"
                ]
                for pl, pdata in sorted(prestigedata.items(), key=lambda x: int(x[0])):
                    r = self._get_role_by_id(interaction.guild, pdata.get("role", 0))
                    pr_lines.append(f"• Prestige {pl}: {r.mention if r else 'No Role'}")
                prestige_value = "\n".join(pr_lines)
                sections.append(("Prestige", prestige_value))
                if len(prestige_value) > 1024:
                    needs_pagination = True

            # FIXED: fmt_bonus now correctly displays the raw data from json
            def fmt_bonus(d):
                if not d:
                    return "None"
                parts = []
                for rid, bonus in d.items():
                    role_obj = self._get_role_by_id(interaction.guild, rid)
                    parts.append(f"• {role_obj.mention if role_obj else '(deleted role)'}: {bonus}")
                return "\n".join(parts)

            msg_bonus = rolebonus.get("msg", {})
            if msg_bonus:
                msg_bonus_value = fmt_bonus(msg_bonus)
                sections.append(("Message XP Bonus Roles", msg_bonus_value))
                if len(msg_bonus_value) > 1024:
                    needs_pagination = True

            voice_bonus = rolebonus.get("voice", {})
            if voice_bonus:
                voice_bonus_value = fmt_bonus(voice_bonus)
                sections.append(("Voice XP Bonus Roles", voice_bonus_value))
                if len(voice_bonus_value) > 1024:
                    needs_pagination = True

            ignored_channels = config.get("ignoredchannels", [])
            if ignored_channels:
                ch_text = " ".join(fmt_channel(c) for c in ignored_channels)
                sections.append(("Ignored Channels", ch_text))
                if len(ch_text) > 1024:
                    needs_pagination = True

            ignored_users = config.get("ignoredusers", [])
            if ignored_users:
                user_text = " ".join(f"<@{u}>" for u in ignored_users)
                sections.append(("Ignored Users", user_text))
                if len(user_text) > 1024:
                    needs_pagination = True

            settings_text = "\n\n".join(
                f"**{section_title}**\n{section_value}" for section_title, section_value in sections
            )
            if len(settings_text) > 1800:
                needs_pagination = True

            if needs_pagination:
                await AdvancedPaginationView.send_paginated_text(
                    interaction=interaction,
                    content=settings_text,
                    title="LevelUp Settings",
                    color=discord.Color.red(),
                    ephemeral=False
                )
                return

            embed = discord.Embed(title="LevelUp Settings", color=discord.Color.red())
            embed.set_author(name=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)

            for section_title, section_value in sections:
                embed.add_field(
                    name=section_title,
                    value=section_value[:1024] if section_value else "None",
                    inline=False
                )

            # --- END OF MAJOR REFACTOR ---

            await interaction.response.send_message(embed=embed)
            return

        # Update settings logic remains the same
        changes = []
        if enabled is not None:
            config["enabled"] = enabled
            changes.append(f"Enabled: {'✅ Yes' if enabled else '❌ No'}")
        
        if cooldown is not None:
            config["cooldown"] = max(1, min(cooldown, 3600))
            changes.append(f"Cooldown: {config['cooldown']}s")
        
        if min_length is not None:
            config["min_length"] = max(1, min(min_length, 100))
            changes.append(f"Min Length: {config['min_length']} chars")
        
        if notify is not None:
            config["notify"] = notify
            changes.append(f"Notifications: {'✅ Yes' if notify else '❌ No'}")
        
        await self.save_levels_data()
        
        embed = discord.Embed(
            title="✅ Settings Updated",
            description="\n".join(changes),
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    @level_group.command(name="roles", description="Manage level roles (Admin only)")
    @app_commands.describe(
        level="The level to assign/remove a role for",
        role="The role to assign at this level (leave empty to remove)",
        action="Whether to add or remove the level role"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def level_roles(
        self, 
        interaction: discord.Interaction, 
        level: int, 
        role: Optional[discord.Role] = None,
        action: Optional[str] = None
    ):
        guild_id = str(interaction.guild.id)
        config = self.get_guild_config(guild_id)
        
        if level < 1 or level > 100:
            await interaction.response.send_message("❌ Level must be between 1 and 100.", ephemeral=True)
            return
        
        level_str = str(level)
        levelroles = config.get("levelroles", {})
        
        if role is None and action != "remove":
            # Show current level roles
            if not levelroles:
                await interaction.response.send_message("📋 No level roles configured.", ephemeral=True)
                return
            
            embed = discord.Embed(title="🎭 Level Roles", color=discord.Color.blue())
            role_list = []
            for lvl, role_id in sorted(levelroles.items(), key=lambda x: int(x[0])):
                role_obj = self._get_role_by_id(interaction.guild, role_id)
                role_name = role_obj.name if role_obj else f"Unknown Role ({role_id})"
                role_list.append(f"Level {lvl}: {role_name}")

            role_text = "\n".join(role_list) if role_list else "No level roles found."
            if len(role_text) > 1800:
                await AdvancedPaginationView.send_paginated_text(
                    interaction=interaction,
                    content=role_text,
                    title="🎭 Level Roles",
                    color=discord.Color.blue(),
                    ephemeral=False
                )
                return

            embed.description = role_text
            await interaction.response.send_message(embed=embed)
            return
        
        if role is None or action == "remove":
            # Remove level role
            if level_str in levelroles:
                removed_role_id = levelroles.pop(level_str)
                removed_role = self._get_role_by_id(interaction.guild, removed_role_id)
                role_name = removed_role.name if removed_role else f"Unknown Role ({removed_role_id})"
                await self.save_levels_data()
                await interaction.response.send_message(f"✅ Removed level {level} role: {role_name}")
            else:
                await interaction.response.send_message(f"❌ No role configured for level {level}.", ephemeral=True)
        else:
            # Add level role
            levelroles[level_str] = role.id
            await self.save_levels_data()
            await interaction.response.send_message(f"✅ Set level {level} role to: {role.mention}")
    
    @level_prestige_group.command(name="settings", description="Manage prestige system (Admin only)")
    @app_commands.describe(
        prestige_level="The prestige level to configure",
        role="The role to assign at this prestige level",
        emoji="Emoji for this prestige level",
        action="Whether to add or remove the prestige level"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def level_prestige(
        self, 
        interaction: discord.Interaction, 
        prestige_level: Optional[int] = None,
        role: Optional[discord.Role] = None,
        emoji: Optional[str] = None,
        action: Optional[str] = None
    ):
        guild_id = str(interaction.guild.id)
        config = self.get_guild_config(guild_id)
        
        prestigedata = config.get("prestigedata", {})
        
        if prestige_level is None:
            # Show current prestige configuration
            if not prestigedata:
                embed = discord.Embed(
                    title="⭐ Prestige System", 
                    description="No prestige levels configured.",
                    color=discord.Color.blue()
                )
            else:
                embed = discord.Embed(title="⭐ Prestige System", color=discord.Color.gold())
                prestige_list = []
                for lvl, data in sorted(prestigedata.items(), key=lambda x: int(x[0])):
                    role_id = data.get("role")
                    role_obj = self._get_role_by_id(interaction.guild, role_id) if role_id else None
                    role_name = role_obj.mention if role_obj else "No Role"
                    emoji_str = data.get("emoji_string", "⭐")
                    prestige_list.append(f"{emoji_str} **Prestige {lvl}**: {role_name}")
                
                embed.description = "\n".join(prestige_list) if prestige_list else "No prestige levels found."
            
            # Show prestige level requirement
            prestigelevel = config.get("prestigelevel", 10)
            prestige_text = (embed.description or "No prestige levels configured.")
            prestige_text += f"\n\n**Prestige Requirement**\nLevel {prestigelevel}"

            if len(prestige_text) > 1800:
                await AdvancedPaginationView.send_paginated_text(
                    interaction=interaction,
                    content=prestige_text,
                    title="⭐ Prestige System",
                    color=embed.color,
                    ephemeral=False
                )
                return

            embed.add_field(
                name="📊 Prestige Requirement",
                value=f"Level {prestigelevel}",
                inline=False
            )
            
            await interaction.response.send_message(embed=embed)
            return
        
        if prestige_level < 1 or prestige_level > 20:
            await interaction.response.send_message("❌ Prestige level must be between 1 and 20.", ephemeral=True)
            return
        
        prestige_str = str(prestige_level)
        
        if action == "remove":
            # Remove prestige level
            if prestige_str in prestigedata:
                prestigedata.pop(prestige_str)
                await self.save_levels_data()
                await interaction.response.send_message(f"✅ Removed prestige level {prestige_level}")
            else:
                await interaction.response.send_message(f"❌ No prestige level {prestige_level} configured.", ephemeral=True)
        else:
            # Add/update prestige level
            if not role and not emoji:
                await interaction.response.send_message("❌ Please provide at least a role or emoji for the prestige level.", ephemeral=True)
                return
            
            if prestige_str not in prestigedata:
                prestigedata[prestige_str] = {}
            
            if role:
                prestigedata[prestige_str]["role"] = role.id
            if emoji:
                prestigedata[prestige_str]["emoji_string"] = emoji
                # Try to get emoji URL if it's a custom emoji
                if emoji.startswith('<:') and emoji.endswith('>'):
                    # Custom emoji format: <:name:id>
                    emoji_id = emoji.split(':')[-1].rstrip('>')
                    prestigedata[prestige_str]["emoji_url"] = f"https://cdn.discordapp.com/emojis/{emoji_id}.png"
                else:
                    # Unicode emoji - use Twemoji CDN
                    try:
                        # Convert emoji to hex codepoint
                        emoji_hex = hex(ord(emoji))[2:]
                        prestigedata[prestige_str]["emoji_url"] = f"https://cdnjs.cloudflare.com/ajax/libs/twemoji/14.0.2/72x72/{emoji_hex}.png"
                    except Exception:
                        pass  # Keep without URL if conversion fails
            
            await self.save_levels_data()
            
            role_text = f" with role {role.mention}" if role else ""
            emoji_text = f" and emoji {emoji}" if emoji else ""
            await interaction.response.send_message(f"✅ Set prestige level {prestige_level}{role_text}{emoji_text}")
    
    @level_prestige_group.command(name="requirement", description="Set the level required for prestige (Admin only)")
    @app_commands.describe(level="The level required to achieve prestige (default: 10)")
    @app_commands.checks.has_permissions(administrator=True)
    async def level_prestigelevel(self, interaction: discord.Interaction, level: int):
        if level < 5 or level > 100:
            await interaction.response.send_message("❌ Prestige level must be between 5 and 100.", ephemeral=True)
            return
        
        guild_id = str(interaction.guild.id)
        config = self.get_guild_config(guild_id)
        config["prestigelevel"] = level
        await self.save_levels_data()
        
        await interaction.response.send_message(f"✅ Set prestige requirement to level {level}")
    
    @level_voice_group.command(name="xp", description="Configure voice XP settings (Admin only)")
    @app_commands.describe(
        enabled="Enable or disable voice XP",
        xp_per_minute="Voice XP gained per minute (default: 1.0)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def level_voicexp(
        self, 
        interaction: discord.Interaction, 
        enabled: bool = None, 
        xp_per_minute: float = None
    ):
        """Configure voice XP settings"""
        try:
            guild_id = str(interaction.guild.id)
            config = self.get_guild_config(guild_id)
            
            # Create or update voice settings
            voice_settings = config.get("voice_settings", {})
            
            changes = []
            if enabled is not None:
                voice_settings["enabled"] = enabled
                changes.append(f"Voice XP {'enabled' if enabled else 'disabled'}")
            
            if xp_per_minute is not None:
                voice_settings["xp_per_minute"] = max(0.1, xp_per_minute)
                changes.append(f"Voice XP set to {xp_per_minute}/minute")
            
            if changes:
                config["voice_settings"] = voice_settings
                await self.save_levels_data()
                
                embed = discord.Embed(
                    title="🎤 Voice XP Settings Updated",
                    description="\n".join(f"• {change}" for change in changes),
                    color=discord.Color.green()
                )
            else:
                # Show current settings
                embed = discord.Embed(
                    title="🎤 Current Voice XP Settings",
                    color=discord.Color.blue()
                )
                embed.add_field(
                    name="Enabled", 
                    value="✅ Yes" if voice_settings.get("enabled", True) else "❌ No", 
                    inline=True
                )
                embed.add_field(
                    name="XP per minute", 
                    value=f"{voice_settings.get('xp_per_minute', 1.0)}", 
                    inline=True
                )
            
            await interaction.response.send_message(embed=embed)
            
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Error configuring voice XP: {e}", 
                ephemeral=True
            )
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Handle voice channel join/leave/move events"""
        if member.bot:
            return
            
        guild_id = str(member.guild.id)
        config = self.get_guild_config(guild_id)
        
        if not config.get("enabled", True):
            return
            
        now = datetime.now(timezone.utc)
        user_id = str(member.id)
        if guild_id not in self.voice_tracking:
            self.voice_tracking[guild_id] = {}

        # User leaves or becomes ineligible
        if (before.channel and not after.channel) or (user_id in self.voice_tracking[guild_id] and not self.can_gain_voice_xp(member, config)):
            if user_id in self.voice_tracking[guild_id]:
                join_time = self.voice_tracking[guild_id].pop(user_id)
                await self.process_voice_xp(member, join_time, now, config)

        # User joins or becomes eligible
        elif (not before.channel and after.channel) or (user_id not in self.voice_tracking[guild_id] and self.can_gain_voice_xp(member, config)):
             if self.can_gain_voice_xp(member, config):
                self.voice_tracking[guild_id][user_id] = now
    
    async def process_voice_xp(self, member, join_time, leave_time, config):
        guild_id = str(member.guild.id)
        user_id = str(member.id)
        
        time_diff_seconds = (leave_time - join_time).total_seconds()
        if time_diff_seconds < 60: return # Must be in voice for at least 1 minute

        # CORRECTED: Update 'voice' with time, and 'xp' with experience
        voice_settings = config.get("voice_settings", {})
        xp_per_minute = voice_settings.get("xp_per_minute", 1.0)
        voice_xp_gained = (time_diff_seconds / 60.0) * xp_per_minute
        
        user_data = self.get_user_data(guild_id, user_id)
        old_level = self.calculate_level_from_xp(user_data.get("xp", 0.0))
        
        # Add time to 'voice', add XP to 'xp'
        user_data["voice"] = user_data.get("voice", 0.0) + time_diff_seconds
        user_data["xp"] = user_data.get("xp", 0.0) + voice_xp_gained
        user_data["last_active"] = leave_time.isoformat()
        
        new_level = self.calculate_level_from_xp(user_data["xp"])
        user_data["level"] = new_level
        
        await self.save_levels_data()
        logging.info(f"{member.display_name} gained {voice_xp_gained:.1f} voice XP over {time_diff_seconds/60.0:.1f} minutes")
        
        # Handle level up notifications
        if new_level > old_level and config.get("notify", True):
             # Logic for pending levelups for voice
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild: return
        guild_id = str(message.guild.id)
        user_id = str(message.author.id)
        config = self.get_guild_config(guild_id)
        if not config.get("enabled", True): return
        if message.channel.id in config.get("ignoredchannels", []): return
        if message.author.id in config.get("ignoredusers", []): return

        min_length = config.get("min_length", 4)
        if len(message.content) < min_length: return
        
        cooldown = config.get("cooldown", 60)
        now = datetime.now(timezone.utc)
        last_key = f"{guild_id}:{user_id}"
        if last_key in self.last_message and (now - self.last_message[last_key]).total_seconds() < cooldown:
            return
        self.last_message[last_key] = now
        
        import random
        
        # Get message XP settings, including the new 'per_char' field
        msgxp = config.get("message_xp", {"min": 15, "max": 25, "per_char": 0.0})
        
        # 1. Calculate the base random XP
        base_xp = random.randint(msgxp.get("min", 15), msgxp.get("max", 25))
        
        # 2. Calculate the bonus XP based on message length
        xp_per_char = msgxp.get("per_char", 0.0)
        length_bonus = 0
        
        if xp_per_char > 0:
            # The bonus only applies to characters beyond the minimum length requirement
            bonus_chars = len(message.content) - min_length
            length_bonus = bonus_chars * xp_per_char
            
            # 3. (Recommended) Cap the bonus to prevent abuse from very long messages.
            # Here, we cap the bonus so it cannot be larger than the max base XP.
            max_bonus = msgxp.get("max", 25)
            length_bonus = min(length_bonus, max_bonus)

        # 4. The final XP gain is the base XP plus the length bonus
        xp_gain = base_xp + length_bonus

        user_data = self.get_user_data(guild_id, user_id)
        
        old_level = self.calculate_level_from_xp(user_data.get("xp", 0.0))
        
        user_data["xp"] = user_data.get("xp", 0.0) + xp_gain
        user_data["messages"] = user_data.get("messages", 0) + 1
        user_data["last_active"] = now.isoformat()
        
        new_level = self.calculate_level_from_xp(user_data["xp"])
        user_data["level"] = new_level
        
        await self.save_levels_data()
        
        if new_level > old_level and config.get("notify", True):
            await self.handle_level_up(message, user_data, old_level, new_level, config)

    async def handle_level_up(self, message, user_data, old_level, new_level, config, from_voice=False):
        """Handle level up notification and role assignment"""
        try:
            # Generate personalized level up message using LLM
            levelup_msg = await self.generate_personalized_levelup_message(
                message, user_data, old_level, new_level, config, from_voice
            )
            if not levelup_msg:
                return

            allowed_mentions = discord.AllowedMentions(users=True, roles=True, everyone=False)
            
            # Send in current channel (immediate notification)
            if config.get("notify", True):
                await message.channel.send(levelup_msg, allowed_mentions=allowed_mentions)
            
            # Also send to designated log channel if configured
            notifylog = config.get("notifylog")
            if notifylog:
                try:
                    notifylog_id = int(notifylog)
                except (TypeError, ValueError):
                    notifylog_id = None

                channel = message.guild.get_channel(notifylog_id) if notifylog_id else None
                if channel and channel.id != message.channel.id:
                    if await self.bot.context_manager.is_channel_llm_blacklisted(message.guild.id, channel.id):
                        logging.info("Skipping level-up notification in LLM-blacklisted log channel. channel_id=%s", channel.id)
                        return
                    await channel.send(levelup_msg, allowed_mentions=allowed_mentions)
                
        except Exception as e:
            logging.error(f"Error handling level up for {message.author}: {e}")

    async def generate_personalized_levelup_message(self, message, user_data, old_level, new_level, config, from_voice=False):
        """Generate a personalized level up message using the LLM"""
        try:
            levelroles = config.get("levelroles", {})
            prestigedata = config.get("prestigedata", {})
            prestigelevel = config.get("prestigelevel", 10)
            
            new_role = None
            new_prestige = None
            is_prestige_reset = False
            achieved_level = new_level
            achieved_prestige_level = None
            total_xp_at_level_up = user_data.get("xp", 0.0)
            
            if str(new_level) in levelroles:
                new_role = self._get_role_by_id(message.guild, levelroles[str(new_level)])

            if new_level >= prestigelevel:
                current_prestige = user_data.get("prestige", 0)
                new_prestige_level = current_prestige + 1
                
                if str(new_prestige_level) in prestigedata:
                    prestige_info = prestigedata[str(new_prestige_level)]
                    if prestige_info.get("role"):
                        new_prestige = self._get_role_by_id(message.guild, prestige_info["role"])
                    
                    user_data["prestige"] = new_prestige_level
                    user_data["xp"] = 0.0 # Reset XP on prestige
                    user_data["voice"] = 0.0
                    user_data["level"] = 0
                    is_prestige_reset = True
                    achieved_prestige_level = new_prestige_level
                    new_level = 0
                    await self.save_levels_data()
            
            # Apply roles before generating message to have them available
            await self.apply_role_changes(message, new_level, new_role, new_prestige, config, is_prestige_reset)

            if await self.bot.context_manager.is_channel_llm_blacklisted(message.guild.id, message.channel.id):
                logging.info("Using fallback level-up message because channel is LLM-blacklisted. channel_id=%s", message.channel.id)
                return await self.generate_fallback_message(
                    message, achieved_level, new_role, new_prestige, is_prestige_reset, config, achieved_prestige_level
                )

            profile_context = await self._levelup_profile_context(message.guild.id, message.author.id)

            level_context_parts = [
                f"User mention: {message.author.mention}",
                f"Display name: {UtilityHelpers.safe_username(message.author)}",
                f"Old level: {old_level}",
                f"Reached level: {achieved_level}",
                f"Total XP at level-up: {total_xp_at_level_up:,.1f}",
            ]
            if from_voice:
                level_context_parts.append("Level up was from VOICE CHAT activity.")
            else:
                level_context_parts.append("Level up was from text messaging.")
            if new_role:
                level_context_parts.append(f"New role earned: {new_role.mention} ({new_role.name})")
            if is_prestige_reset:
                level_context_parts.append(f"This is a PRESTIGE. User is now prestige {achieved_prestige_level} and reset to level 0.")
                if new_prestige:
                    level_context_parts.append(f"New prestige role earned: {new_prestige.mention} ({new_prestige.name})")

            prompt = f"""Write exactly one Discord level-up notification.

Level-up facts:
{chr(10).join(f"- {part}" for part in level_context_parts)}

Stored user context:
{profile_context}

Rules:
- Keep it under 450 characters.
- Mention the user exactly once using the provided user mention.
- If a new role or prestige role is listed, mention it using the provided role mention.
- Make the line feel specific to this user and achievement. Use the stored user context if it helps.
- Avoid generic template phrases like "Great job" unless there is also a specific detail.
- Celebrate the achievement; do not answer or quote the user's message.
- Do not start with your bot name, a username, "Assistant:", "Bot:", or a User ID.
- No headings, code blocks, or multi-message output."""

            settings = await self.bot.context_manager.get_guild_and_channel_settings(message.guild.id, message.channel.id)
            model = settings.get("model", self.bot.config.MAIN_LLM_MODEL)
            behavior_prompt = settings.get("behavior_prompt", self.bot.config.BEHAVIOR_PROMPT)
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are writing as this Discord bot and must preserve its configured personality and style.\n\n"
                        f"Configured bot behavior/personality:\n{behavior_prompt}\n\n"
                        "For this task, write a short, punchy Discord level-up announcement. "
                        "Use Discord mentions exactly as provided. Return only the announcement text."
                    ),
                },
                {"role": "user", "content": prompt},
            ]
            
            # Call the LLM
            response = await asyncio.wait_for(
                self.bot.llm_provider.create_completion(
                    model=model,
                    messages=messages,
                    max_tokens=180,
                    temperature=0.8
                ),
                timeout=45
            )
            
            if response and response.choices:
                choice = response.choices[0]
                finish_reason = str(getattr(choice, "finish_reason", "")).lower()
                if finish_reason in {"length", "max_tokens", "max_output_tokens"}:
                    logging.warning("Level-up LLM response was truncated; using fallback message.")
                    return await self.generate_fallback_message(
                        message, achieved_level, new_role, new_prestige, is_prestige_reset, config, achieved_prestige_level
                    )

                raw_content = getattr(choice.message, "content", None)
                if raw_content:
                    levelup_msg = self._sanitize_levelup_message(
                        raw_content,
                        message,
                        new_role=new_role,
                        new_prestige=new_prestige,
                        is_prestige_reset=is_prestige_reset
                    )
                    if levelup_msg:
                        return levelup_msg

                return await self.generate_fallback_message(
                    message, achieved_level, new_role, new_prestige, is_prestige_reset, config, achieved_prestige_level
                )
            else:
                # Fallback to default message
                return await self.generate_fallback_message(
                    message, achieved_level, new_role, new_prestige, is_prestige_reset, config, achieved_prestige_level
                )
                
        except Exception as e:
            logging.error(f"Error generating personalized level up message: {e}")
            # Fallback to default message
            return await self.generate_fallback_message(
                message,
                locals().get("achieved_level", new_level),
                locals().get("new_role"),
                locals().get("new_prestige"),
                locals().get("is_prestige_reset", False),
                config,
                locals().get("achieved_prestige_level")
            )
    
    async def _levelup_profile_context(self, guild_id, user_id, max_chars: int = 900) -> str:
        try:
            guild_data = await self.bot.store.get_guild_data(str(guild_id))
            stored_user = guild_data.get("users", {}).get(str(user_id), {})
            context_parts = []
            manual_note = str(stored_user.get("manual_note") or "").strip()
            ai_summary = str(stored_user.get("ai_summary") or "").strip()
            if manual_note:
                context_parts.append(f"Manual note: {manual_note}")
            if ai_summary:
                context_parts.append(f"AI profile summary: {ai_summary}")
            if not context_parts:
                return "- No stored user profile context yet."
            return UtilityHelpers.truncate_string("\n".join(context_parts), max_chars)
        except Exception as e:
            logging.warning("Could not load level-up profile context for user %s: %s", user_id, e)
            return "- No stored user profile context available."

    def _sanitize_levelup_message(
        self,
        content: str,
        message: discord.Message,
        *,
        new_role: Optional[discord.Role] = None,
        new_prestige: Optional[discord.Role] = None,
        is_prestige_reset: bool = False
    ) -> str:
        """Keep generated level-up notifications short and free of speaker labels."""
        content = content.strip()
        if content.startswith("```"):
            content = content.strip("`").strip()

        bot_user = self.bot.user
        if bot_user:
            bot_id = str(bot_user.id)
            names = {
                getattr(bot_user, "name", ""),
                getattr(bot_user, "display_name", ""),
                str(bot_user),
            }
            if message.guild and message.guild.me:
                names.add(getattr(message.guild.me, "display_name", ""))
            for name in {name for name in names if name}:
                escaped = re.escape(name)
                content = re.sub(
                    rf"^\s*(?:\*\*)?{escaped}(?:\*\*)?\s*(?:\((?:User ID|ID):\s*{bot_id}\))?\s*[:\-]\s*",
                    "",
                    content,
                    count=1,
                    flags=re.IGNORECASE
                ).lstrip()

        content = re.sub(r"^\s*(?:assistant|bot)\s*[:\-]\s*", "", content, count=1, flags=re.IGNORECASE).strip()
        user_mention_pattern = re.compile(rf"<@!?{re.escape(str(message.author.id))}>")
        if not user_mention_pattern.search(content):
            content = f"{message.author.mention} {content}"
        if new_role and new_role.mention not in content:
            content = f"{content} You've earned {new_role.mention}."
        if is_prestige_reset and new_prestige and new_prestige.mention not in content:
            content = f"{content} Prestige role: {new_prestige.mention}."

        if len(content) > 900:
            logging.warning("Level-up LLM response was too long; truncating to a safe Discord size.")
            content = UtilityHelpers.truncate_string(content, 900)

        return content

    async def generate_fallback_message(self, message, new_level, new_role, new_prestige, is_prestige_reset, config, prestige_level=None):
        """Generate a fallback message when LLM is unavailable"""
        
        if is_prestige_reset:
            prestige_level = prestige_level if prestige_level is not None else "a new prestige"
            msg = f"🌟✨ PRESTIGE ACHIEVED! ✨🌟 {message.author.mention} has reached the ultimate milestone and earned prestige level {prestige_level}!"
            if new_prestige:
                msg += f" Welcome to the {new_prestige.mention} ranks!"
        else:
            msg = f"🎉 {message.author.mention} just reached level {new_level}! Great job!"
            if new_role:
                msg += f" You've earned the {new_role.mention} role!"
        
        return msg
    
    async def apply_role_changes(self, message, new_level, new_role, new_prestige, config, is_prestige_reset):
        """Apply role changes for level ups and prestige"""
        try:
            autoremove = config.get("autoremove", True)
            
            if new_role:
                await message.author.add_roles(new_role, reason=f"Level up to {new_level}")
            
            if new_prestige:
                await message.author.add_roles(new_prestige, reason="Prestige achievement")
            
            if autoremove:
                levelroles = config.get("levelroles", {})
                roles_to_remove = []
                for level_str, role_id in levelroles.items():
                    level_int = int(level_str)
                    # Remove if it's an old level role, or if prestiging (remove all level roles)
                    if (not is_prestige_reset and level_int < new_level) or is_prestige_reset:
                        if str(role_id) != (str(new_role.id) if new_role else None):
                            old_role = self._get_role_by_id(message.guild, role_id)
                            if old_role and old_role in message.author.roles:
                                roles_to_remove.append(old_role)
                if roles_to_remove:
                    await message.author.remove_roles(*roles_to_remove, reason="Level up/Prestige")

        except discord.Forbidden:
            logging.warning(f"Cannot apply role changes for {message.author} - missing permissions")
        except Exception as e:
            logging.error(f"Error applying role changes for {message.author}: {e}")

async def setup(bot):
    await bot.add_cog(LevelUpCommands(bot))
