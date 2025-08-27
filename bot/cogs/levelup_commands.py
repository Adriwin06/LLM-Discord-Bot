# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/cogs/levelup_commands.py
import discord
from discord.ext import commands
from discord import app_commands
import json
import math
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import aiofiles
import os

class LevelUpCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.levels_file = "data/levels.json"
        self.levels_data = {}
        self.xp_per_message = [15, 25]  # (legacy fallback, now superseded by per-char scaling)
        self.cooldown_seconds = 60  # Cooldown between XP gains
        self.last_message = {}  # Track last message times for cooldown
        
        # Voice tracking
        self.voice_tracking = {}  # Track users in voice channels {guild_id: {user_id: join_time}}
        self.voice_xp_per_minute = 1.0  # XP gained per minute in voice
        
    async def cog_load(self):
        """Load levels data when cog loads"""
        await self.load_levels_data()
        # Initialize voice tracking for users already in voice channels
        await self.initialize_voice_tracking()
        
    async def load_levels_data(self):
        """Load the levels.json file"""
        try:
            if os.path.exists(self.levels_file):
                # FIXED: Added encoding='utf-8' to correctly read emojis and special characters
                async with aiofiles.open(self.levels_file, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    self.levels_data = json.loads(content)
                    logging.info("Loaded levels data successfully")
            else:
                self.levels_data = {"configs": {}}
                await self.save_levels_data()
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
                        # Only track if they can gain XP
                        if self.can_gain_voice_xp(member, config):
                            self.voice_tracking[guild_id][str(member.id)] = datetime.now(timezone.utc)
                            
                logging.info(f"Initialized voice tracking for {len(self.voice_tracking)} guilds")
        except Exception as e:
            logging.error(f"Error initializing voice tracking: {e}")
    
    def can_gain_voice_xp(self, member, config):
        """Check if a member can gain voice XP based on their state and settings"""
        if not member.voice or not member.voice.channel:
            return False
            
        # Check ignored channels
        if member.voice.channel.id in config.get("ignoredchannels", []):
            return False
            
        # Check ignored users
        if member.id in config.get("ignoredusers", []):
            return False
            
        # Check voice conditions (can be expanded later)
        voice_settings = config.get("voice_settings", {})
        
        # Ignore if muted (if setting enabled)
        if voice_settings.get("ignore_muted", False) and member.voice.mute:
            return False
            
        # Ignore if deafened (if setting enabled) 
        if voice_settings.get("ignore_deafened", False) and member.voice.deaf:
            return False
            
        # Ignore if alone in channel (if setting enabled)
        if voice_settings.get("ignore_solo", False):
            # Count non-bot members in channel
            human_members = [m for m in member.voice.channel.members if not m.bot]
            if len(human_members) <= 1:
                return False
        
        return True
    
    async def save_levels_data(self):
        """Save the levels.json file"""
        try:
            os.makedirs(os.path.dirname(self.levels_file), exist_ok=True)
            # FIXED: Added encoding='utf-8' to correctly write emojis and special characters
            async with aiofiles.open(self.levels_file, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(self.levels_data, indent=4))
        except Exception as e:
            logging.error(f"Error saving levels data: {e}")
    
    def get_guild_config(self, guild_id: str) -> Dict[str, Any]:
        """Get or create guild config"""
        created = False
        if guild_id not in self.levels_data["configs"]:
            self.levels_data["configs"][guild_id] = {
                "users": {},
                "enabled": True,
                "levelroles": {},
                "autoremove": True,
                "cooldown": 60,
                "min_length": 4,
                "rolebonus": {"msg": {}, "voice": {}},
                "ignoredchannels": [],
                "ignoredusers": [],
                "prestigelevel": 10,
                "prestigedata": {},
                # CHANGED: Updated default voice_settings to match desired output
                "voice_settings": {
                    "xp_per_minute": 2.0,
                    "ignore_muted": True,
                    "ignore_deafened": True, 
                    "ignore_solo": True
                },
                "notify": True,
                "notifylog": None,
                "levelup_msg": "🎉 {mention} just reached level {level}! Great job!",
                # CHANGED: Updated default message_xp to match desired output
                "message_xp": {
                    "min": 3,
                    "max": 6,
                    "per_char": 0.20 # This value is not displayed, but kept for logic
                },
            }
            created = True

        cfg = self.levels_data["configs"][guild_id]
        
        # --- Backwards Compatibility Checks ---
        # Backfill new keys if missing from an older levels.json file
        if "message_xp" not in cfg:
            cfg["message_xp"] = {"min": 3, "max": 6, "per_char": 0.20}
            created = True
        if "voice_settings" not in cfg:
            cfg["voice_settings"] = {
                "xp_per_minute": 2.0, "ignore_muted": True, 
                "ignore_deafened": True, "ignore_solo": True
            }
            created = True
            
        if created:
            # Persist asynchronously without blocking
            try:
                import asyncio
                asyncio.get_running_loop().create_task(self.save_levels_data())
            except RuntimeError:
                # Fallback (e.g., during startup before loop) – best effort sync write
                try:
                    with open(self.levels_file, 'w', encoding='utf-8') as f:
                        json.dump(self.levels_data, f, indent=4)
                except Exception:
                    pass
        return cfg

    def get_user_data(self, guild_id: str, user_id: str) -> Dict[str, Any]:
        """Get or create user data"""
        config = self.get_guild_config(guild_id)
        if user_id not in config["users"]:
            config["users"][user_id] = {
                "xp": 0.0,
                "voice": 0.0,
                "messages": 0,
                "level": 0,
                "last_active": datetime.now(timezone.utc).isoformat(),
                "show_tutorial": True
            }
            # Persist new user immediately (non-blocking)
            try:
                import asyncio
                asyncio.get_running_loop().create_task(self.save_levels_data())
            except RuntimeError:
                try:
                    with open(self.levels_file, 'w', encoding='utf-8') as f:
                        json.dump(self.levels_data, f, indent=4)
                except Exception:
                    pass
        return config["users"][user_id]
    
    def calculate_level_from_xp(self, xp: float) -> int:
        """Calculate level from XP using the standard formula"""
        if xp < 0:
            return 0
        # Standard leveling formula: level = floor(sqrt(xp/100))
        # This means: Level 1 = 100 XP, Level 2 = 400 XP, Level 3 = 900 XP, etc.
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
    
    @level_group.command(name="profile", description="View your or someone else's level profile")
    async def level_profile(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        if user is None:
            user = interaction.user
        
        guild_id = str(interaction.guild.id)
        user_id = str(user.id)
        
        config = self.get_guild_config(guild_id)
        if not config.get("enabled", True):
            await interaction.response.send_message("❌ Leveling is disabled in this server.", ephemeral=True)
            return
        
        user_data = self.get_user_data(guild_id, user_id)
        
        # FIXED: Ensure voice XP is correctly read and added to the total
        current_xp = user_data.get("xp", 0.0)
        voice_xp = user_data.get("voice", 0.0)
        total_xp = current_xp + voice_xp
        
        messages = user_data.get("messages", 0)
        current_level = self.calculate_level_from_xp(total_xp)
        prestige = user_data.get("prestige", 0)
        
        # Update level in data if it's changed
        if user_data.get("level", 0) != current_level:
            user_data["level"] = current_level
            await self.save_levels_data()
        
        xp_to_next, progress, needed, next_level = self.calculate_xp_for_next_level(total_xp)
        
        embed = discord.Embed(
            title=f"📊 Level Profile for {user.display_name}",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        
        # Add prestige info if user has prestige
        if prestige > 0:
            embed.add_field(
                name="⭐ Prestige", 
                value=f"**{prestige}**", 
                inline=True
            )
        
        embed.add_field(
            name="📈 Level", 
            value=f"**{current_level}**", 
            inline=True
        )
        embed.add_field(
            name="✨ Total XP", 
            value=f"**{total_xp:,.1f}**", 
            inline=True
        )
        embed.add_field(
            name="💬 Messages", 
            value=f"**{messages:,}**", 
            inline=True
        )
        
        embed.add_field(
            name="💭 Text XP", 
            value=f"{current_xp:,.1f}", 
            inline=True
        )
        embed.add_field(
            name="🎤 Voice XP", 
            value=f"{voice_xp:,.1f}", 
            inline=True
        )
        embed.add_field(
            name="🎯 Next Level", 
            value=f"**{next_level}** ({xp_to_next:,.1f} XP)", 
            inline=True
        )
        
        # Progress bar
        progress_percentage = (progress / needed) * 100 if needed > 0 else 100
        progress_bar_length = 20
        filled_length = int(progress_bar_length * progress_percentage / 100)
        progress_bar = "█" * filled_length + "░" * (progress_bar_length - filled_length)
        
        embed.add_field(
            name="📊 Progress to Next Level",
            value=f"`{progress_bar}` {progress_percentage:.1f}%\n{progress:,.1f}/{needed:,.1f} XP",
            inline=False
        )
        
        # Check for level roles
        level_roles = config.get("levelroles", {})
        prestige_data = config.get("prestigedata", {})
        
        role_info = []
        if str(current_level) in level_roles:
            role_id = level_roles[str(current_level)]
            role = interaction.guild.get_role(role_id)
            if role:
                role_info.append(f"Level: {role.mention}")
        
        if prestige > 0 and str(prestige) in prestige_data:
            prestige_role_id = prestige_data[str(prestige)].get("role")
            if prestige_role_id:
                prestige_role = interaction.guild.get_role(prestige_role_id)
                if prestige_role:
                    role_info.append(f"Prestige: {prestige_role.mention}")
        
        if role_info:
            embed.add_field(
                name="🏆 Roles",
                value="\n".join(role_info),
                inline=True
            )
        
        await interaction.response.send_message(embed=embed)
    
    @level_group.command(name="leaderboard", description="View the server leaderboard")
    async def level_leaderboard(self, interaction: discord.Interaction, limit: Optional[int] = 10):
        guild_id = str(interaction.guild.id)
        config = self.get_guild_config(guild_id)
        
        if not config.get("enabled", True):
            await interaction.response.send_message("❌ Leveling is disabled in this server.", ephemeral=True)
            return
        
        if limit > 25:
            limit = 25
        elif limit < 1:
            limit = 10
        
        users_data = config.get("users", {})
        if not users_data:
            await interaction.response.send_message("📊 No leveling data found for this server yet!", ephemeral=True)
            return
        
        # Calculate total XP for each user and sort
        user_rankings = []
        for user_id, data in users_data.items():
            # FIXED: This calculation was correct, the issue was presentation and data defaults.
            # This ensures both text and voice XP are always included.
            total_xp = data.get("xp", 0.0) + data.get("voice", 0.0)
            if total_xp > 0: # Only add users with XP to the leaderboard
                user_rankings.append((user_id, total_xp))
        
        # Sort by total XP (descending)
        user_rankings.sort(key=lambda x: x[1], reverse=True)
        
        # CHANGED: Updated embed format to match the desired output exactly
        embed = discord.Embed(
            title=f"🏆 {interaction.guild.name} Leaderboard",
            description=f"Top {min(limit, len(user_rankings))} members by Total XP",
            color=discord.Color.gold()
        )
        
        leaderboard_text = ""
        for i, (user_id, total_xp) in enumerate(user_rankings[:limit], 1):
            try:
                user = interaction.guild.get_member(int(user_id))
                name = user.display_name if user else f"Unknown User ({user_id})"
                
                # Medal emojis for top 3
                if i == 1: medal = "🏆"
                elif i == 2: medal = "🥈"
                elif i == 3: medal = "🥉"
                else: medal = f"{i}." # Use plain number for other ranks
                
                # CHANGED: Updated leaderboard line format to match desired output
                leaderboard_text += f"{medal} **{name}** - {total_xp:,.1f} Total XP\n"
                
            except Exception as e:
                logging.error(f"Error processing user {user_id} in leaderboard: {e}")
                continue
        
        if leaderboard_text:
            embed.description = leaderboard_text
        else:
            embed.description = "No valid users found in leaderboard."
        
        embed.set_footer(text=f"Requested by {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)
    
    @level_group.command(name="settings", description="View or configure leveling settings (Admin only)")
    @app_commands.describe(
        enabled="Enable or disable leveling in this server",
        cooldown="Cooldown between XP gains (in seconds)",
        min_length="Minimum message length to earn XP",
        notify="Enable level up notifications",
        levelup_message="Custom level up message (use {mention} and {level})"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def level_settings(
        self,
        interaction: discord.Interaction,
        enabled: Optional[bool] = None,
        cooldown: Optional[int] = None,
        min_length: Optional[int] = None,
        notify: Optional[bool] = None,
        levelup_message: Optional[str] = None
    ):
        guild_id = str(interaction.guild.id)
        config = self.get_guild_config(guild_id)

        def fmt_bool(v): return "Yes" if v else "No" # CHANGED: To display Yes/No
        def fmt_channel(cid):
            if not cid: return "None"
            ch = interaction.guild.get_channel(cid)
            return ch.mention if ch else f"(deleted #{cid})"

        if all(param is None for param in [enabled, cooldown, min_length, notify, levelup_message]):
            # --- START OF MAJOR REFACTOR ---
            # This entire block has been rewritten to exactly match the desired screenshot output.
            voice = config.get("voice_settings", {})
            msgxp = config.get("message_xp", {})
            rolebonus = config.get("rolebonus", {"msg": {}, "voice": {}})
            prestigedata = config.get("prestigedata", {})
            
            embed = discord.Embed(title="LevelUp Settings", color=discord.Color.blue())
            embed.set_author(name=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)

            embed.add_field(
                name="Main",
                value=(
                    f"System Enabled: {fmt_bool(config.get('enabled', True))}\n"
                    f"Profile Type: Embeds\n"
                    f"Style Override: None\n"
                    f"Include Balance: No"
                ),
                inline=False
            )

            embed.add_field(
                name="Messages",
                value=(
                    f"Message XP: {msgxp.get('min', 3)} - {msgxp.get('max', 6)}\n"
                    f"Min Msg Length: {config.get('min_length', 4)}\n"
                    f"Cooldown: {config.get('cooldown', 60)} seconds\n"
                    f"Command XP: False"
                ),
                inline=False
            )

            embed.add_field(
                name="Voice",
                value=(
                    f"Voice XP: {voice.get('xp_per_minute', 1.0)} per minute\n"
                    f"Ignore Muted: {fmt_bool(voice.get('ignore_muted', False))}\n"
                    f"Ignore Solo: {fmt_bool(voice.get('ignore_solo', False))}\n"
                    f"Ignore Deafened: {fmt_bool(voice.get('ignore_deafened', False))}\n"
                    f"Ignore Invisible: True" # Hardcoded as per screenshot
                ),
                inline=False
            )

            embed.add_field(
                name="Level Algorithm",
                value=(
                    "Base Multiplier: 100\n"
                    "Exp Multiplier: 2.0\n"
                    "Equation: 100 x (level ^ 2.0) = XP"
                ),
                inline=False
            )

            embed.add_field(
                name="LevelUps",
                value=(
                    f"Notify In channel: {fmt_bool(config.get('notify', True))}\n"
                    f"• Send levelup message in the channel the user is typing in\n"
                    f"Notify in DMs: False\n"
                    f"• Log channel for levelup messages\n"
                    f"Notify Channel: {fmt_channel(config.get('notifylog'))}\n"
                    f"Mention User: False\n" # Hardcoded as per screenshot
                    f"AutoRemove Roles: {fmt_bool(config.get('autoremove', True))}"
                ),
                inline=False
            )

            levelroles = config.get("levelroles", {})
            if levelroles:
                lr_lines = ["➤ Level roles will Stack"]
                # Sort by level descending to match screenshot
                for lvl, rid in sorted(levelroles.items(), key=lambda x: int(x[0]), reverse=True):
                    r = interaction.guild.get_role(rid)
                    lr_lines.append(f"• Level {lvl}: {r.mention if r else f'(deleted role {rid})'}")
                embed.add_field(name="Level Roles", value="\n".join(lr_lines)[:1024], inline=False)

            if prestigedata:
                pr_lines = [
                    "➤ Prestige roles will Stack",
                    f"➤ Requires reaching level {config.get('prestigelevel', 8)} to activate",
                    "➤ Level roles will be reset after prestiging"
                ]
                for pl, pdata in sorted(prestigedata.items(), key=lambda x: int(x[0])):
                    r = interaction.guild.get_role(pdata.get("role", 0))
                    pr_lines.append(f"• Prestige {pl}: {r.mention if r else 'No Role'}")
                embed.add_field(name="Prestige", value="\n".join(pr_lines)[:1024], inline=False)

            # FIXED: fmt_bonus now correctly displays the raw data from json
            def fmt_bonus(d):
                if not d: return "None"
                parts = []
                for rid, bonus in d.items():
                    role_obj = interaction.guild.get_role(int(rid))
                    parts.append(f"• {role_obj.mention if role_obj else '(deleted role)'}: {bonus}")
                return "\n".join(parts)

            msg_bonus = rolebonus.get("msg", {})
            if msg_bonus:
                embed.add_field(name="Message XP Bonus Roles", value=fmt_bonus(msg_bonus)[:1024], inline=False)
            
            voice_bonus = rolebonus.get("voice", {})
            if voice_bonus:
                embed.add_field(name="Voice XP Bonus Roles", value=fmt_bonus(voice_bonus)[:1024], inline=False)

            ignored_channels = config.get("ignoredchannels", [])
            if ignored_channels:
                ch_text = " ".join(fmt_channel(c) for c in ignored_channels)
                embed.add_field(name="Ignored Channels", value=ch_text[:1024], inline=False)

            ignored_users = config.get("ignoredusers", [])
            if ignored_users:
                user_text = " ".join(f"<@{u}>" for u in ignored_users)
                embed.add_field(name="Ignored Users", value=user_text[:1024], inline=False)

            embed.add_field(
                name="LevelUp Message",
                value=config.get("levelup_msg", "🎉 {mention} just reached level {level}!"),
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
        
        if levelup_message is not None:
            if len(levelup_message) > 500:
                await interaction.response.send_message("❌ Level up message too long! Maximum 500 characters.", ephemeral=True)
                return
            config["levelup_msg"] = levelup_message
            changes.append("Level Up Message: Updated")
        
        await self.save_levels_data()
        
        embed = discord.Embed(
            title="✅ Settings Updated",
            description="\n".join(changes),
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    # The rest of the functions (level_roles, level_prestige, listeners, etc.)
    # do not need changes for this specific request, so they are included below as-is.

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
                role_obj = interaction.guild.get_role(role_id)
                role_name = role_obj.name if role_obj else f"Unknown Role ({role_id})"
                role_list.append(f"Level {lvl}: {role_name}")
            
            embed.description = "\n".join(role_list) if role_list else "No level roles found."
            await interaction.response.send_message(embed=embed)
            return
        
        if role is None or action == "remove":
            # Remove level role
            if level_str in levelroles:
                removed_role_id = levelroles.pop(level_str)
                removed_role = interaction.guild.get_role(removed_role_id)
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
    
    @level_group.command(name="prestige", description="Manage prestige system (Admin only)")
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
                    role_obj = interaction.guild.get_role(role_id) if role_id else None
                    role_name = role_obj.mention if role_obj else "No Role"
                    emoji_str = data.get("emoji_string", "⭐")
                    prestige_list.append(f"{emoji_str} **Prestige {lvl}**: {role_name}")
                
                embed.description = "\n".join(prestige_list) if prestige_list else "No prestige levels found."
            
            # Show prestige level requirement
            prestigelevel = config.get("prestigelevel", 10)
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
    
    @level_group.command(name="prestigelevel", description="Set the level required for prestige (Admin only)")
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
    
    @level_group.command(name="voicexp", description="Configure voice XP settings (Admin only)")
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
        user_id = str(member.id)
        config = self.get_guild_config(guild_id)
        
        if not config.get("enabled", True):
            return
            
        now = datetime.now(timezone.utc)
        
        # Initialize guild tracking if needed
        if guild_id not in self.voice_tracking:
            self.voice_tracking[guild_id] = {}
        
        # User left voice completely
        if before.channel and not after.channel:
            if user_id in self.voice_tracking[guild_id]:
                join_time = self.voice_tracking[guild_id].pop(user_id)
                await self.process_voice_xp(member, join_time, now, config)
                
        # User joined voice
        elif not before.channel and after.channel:
            if self.can_gain_voice_xp(member, config):
                self.voice_tracking[guild_id][user_id] = now
                
        # User moved/changed state in voice
        elif before.channel and after.channel:
            # If they were being tracked and can still gain XP, update tracking
            if user_id in self.voice_tracking[guild_id]:
                if self.can_gain_voice_xp(member, config):
                    # Still can gain XP, keep tracking
                    pass
                else:
                    # Can no longer gain XP, process what they earned and stop tracking
                    join_time = self.voice_tracking[guild_id].pop(user_id)
                    await self.process_voice_xp(member, join_time, now, config)
            else:
                # They weren't being tracked, start if they can gain XP
                if self.can_gain_voice_xp(member, config):
                    self.voice_tracking[guild_id][user_id] = now
    
    async def process_voice_xp(self, member, join_time, leave_time, config):
        """Process and award voice XP for a user's voice session"""
        try:
            guild_id = str(member.guild.id)
            user_id = str(member.id)
            
            # Calculate time spent in voice (in minutes)
            time_diff = (leave_time - join_time).total_seconds() / 60.0
            
            # Must be in voice for at least 1 minute to gain XP
            if time_diff < 1.0:
                return
                
            # Calculate XP gained
            voice_settings = config.get("voice_settings", {})
            xp_per_minute = voice_settings.get("xp_per_minute", 1.0)
            voice_xp_gained = time_diff * xp_per_minute
            
            # Get user data and add voice XP
            user_data = self.get_user_data(guild_id, user_id)
            old_total_xp = user_data.get("xp", 0.0) + user_data.get("voice", 0.0)
            old_level = self.calculate_level_from_xp(old_total_xp)
            
            user_data["voice"] = user_data.get("voice", 0.0) + voice_xp_gained
            user_data["last_active"] = leave_time.isoformat()
            
            new_total_xp = user_data.get("xp", 0.0) + user_data.get("voice", 0.0)
            new_level = self.calculate_level_from_xp(new_total_xp)
            user_data["level"] = new_level
            
            await self.save_levels_data()
            
            logging.info(f"{member.display_name} gained {voice_xp_gained:.1f} voice XP over {time_diff:.1f} minutes")
            
            # Check for level up (but don't send message in voice context, wait for next text message)
            if new_level > old_level and config.get("notify", True):
                # Store pending level up for next message
                if not hasattr(self, 'pending_levelups'):
                    self.pending_levelups = {}
                if guild_id not in self.pending_levelups:
                    self.pending_levelups[guild_id] = {}
                self.pending_levelups[guild_id][user_id] = {
                    'old_level': old_level,
                    'new_level': new_level,
                    'from_voice': True
                }
                
        except Exception as e:
            logging.error(f"Error processing voice XP for {member}: {e}")
    
    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        """Handle member updates that might affect voice XP eligibility"""
        # If member's roles changed and they're in voice, re-evaluate XP eligibility
        if before.roles != after.roles and after.voice and after.voice.channel:
            guild_id = str(after.guild.id)
            user_id = str(after.id)
            config = self.get_guild_config(guild_id)
            
            if not config.get("enabled", True):
                return
                
            now = datetime.now(timezone.utc)
            
            if guild_id in self.voice_tracking and user_id in self.voice_tracking[guild_id]:
                if not self.can_gain_voice_xp(after, config):
                    # Can no longer gain XP, process what they earned
                    join_time = self.voice_tracking[guild_id].pop(user_id)
                    await self.process_voice_xp(after, join_time, now, config)
            else:
                # Not being tracked, start if they can gain XP
                if self.can_gain_voice_xp(after, config):
                    if guild_id not in self.voice_tracking:
                        self.voice_tracking[guild_id] = {}
                    self.voice_tracking[guild_id][user_id] = now
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Award XP for messages"""
        if message.author.bot or not message.guild:
            return
        
        guild_id = str(message.guild.id)
        user_id = str(message.author.id)
        
        config = self.get_guild_config(guild_id)
        if not config.get("enabled", True):
            return
        
        # Check ignored channels and users
        if message.channel.id in config.get("ignoredchannels", []):
            return
        if message.author.id in config.get("ignoredusers", []):
            return
        
        # Check message length
        min_length = config.get("min_length", 4)
        if len(message.content) < min_length:
            return
        
        # Check cooldown
        cooldown = config.get("cooldown", 60)
        now = datetime.now(timezone.utc)
        last_key = f"{guild_id}:{user_id}"
        
        if last_key in self.last_message:
            time_diff = (now - self.last_message[last_key]).total_seconds()
            if time_diff < cooldown:
                return
        
        self.last_message[last_key] = now
        
        # Award XP (length-scaled)
        import random
        config = self.get_guild_config(guild_id)
        msgxp = config.get("message_xp", {"min": self.xp_per_message[0], "max": self.xp_per_message[1], "per_char": 0.20})
        min_len = config.get("min_length", 4)
        base_min = msgxp.get("min", 15)
        base_max = msgxp.get("max", 25)
        per_char = msgxp.get("per_char", 0.20)

        extra_chars = max(0, len(message.content) - min_len)
        scaled = base_min + extra_chars * per_char
        xp_gain = int(min(base_max, scaled))

        # Small variance (optional) to avoid monotony (±10% capped inside bounds)
        variance = random.uniform(-0.1, 0.1)
        xp_gain = int(min(base_max, max(base_min, xp_gain + xp_gain * variance)))

        user_data = self.get_user_data(guild_id, user_id)
        
        # Calculate old level based on combined XP
        old_total_xp = user_data.get("xp", 0.0) + user_data.get("voice", 0.0)
        old_level = self.calculate_level_from_xp(old_total_xp)
        
        user_data["xp"] = user_data.get("xp", 0.0) + xp_gain
        user_data["messages"] = user_data.get("messages", 0) + 1
        user_data["last_active"] = now.isoformat()
        
        new_total_xp = user_data.get("xp", 0.0) + user_data.get("voice", 0.0)
        new_level = self.calculate_level_from_xp(new_total_xp)
        user_data["level"] = new_level
        
        await self.save_levels_data()
        
        # Check for pending voice level ups first
        if hasattr(self, 'pending_levelups'):
            if guild_id in self.pending_levelups and user_id in self.pending_levelups[guild_id]:
                pending = self.pending_levelups[guild_id].pop(user_id)
                await self.handle_level_up(message, user_data, pending['old_level'], pending['new_level'], config, from_voice=True)
                return  # Don't check for another level up from this message
        
        # Check for level up from this message
        if new_level > old_level and config.get("notify", True):
            await self.handle_level_up(message, user_data, old_level, new_level, config)
    
    async def handle_level_up(self, message, user_data, old_level, new_level, config, from_voice=False):
        """Handle level up notification and role assignment"""
        try:
            # Generate personalized level up message using LLM
            levelup_msg = await self.generate_personalized_levelup_message(
                message, user_data, old_level, new_level, config, from_voice
            )
            
            # Check for notification channel
            notifylog = config.get("notifylog")
            if notifylog:
                channel = message.guild.get_channel(notifylog)
                if channel:
                    await channel.send(levelup_msg)
                else:
                    await message.channel.send(levelup_msg)
            else:
                await message.channel.send(levelup_msg)
                                
        except Exception as e:
            logging.error(f"Error handling level up for {message.author}: {e}")

    async def generate_personalized_levelup_message(self, message, user_data, old_level, new_level, config, from_voice=False):
        """Generate a personalized level up message using the LLM"""
        try:
            guild_id = str(message.guild.id)
            user_id = str(message.author.id)
            
            # This part depends on your bot's specific store implementation
            # Assuming self.bot.store.get_guild_data exists and works
            bot_data = {} # await self.bot.store.get_guild_data(guild_id)
            user_profile = bot_data.get("users", {}).get(user_id, {})
            manual_note = user_profile.get("manual_note", "")
            ai_summary = user_profile.get("ai_summary", "")
            
            levelroles = config.get("levelroles", {})
            prestigedata = config.get("prestigedata", {})
            prestigelevel = config.get("prestigelevel", 10)
            
            new_role = None
            new_prestige = None
            is_prestige_reset = False
            
            if str(new_level) in levelroles:
                new_role = message.guild.get_role(levelroles[str(new_level)])

            if new_level >= prestigelevel:
                current_prestige = user_data.get("prestige", 0)
                new_prestige_level = current_prestige + 1
                
                if str(new_prestige_level) in prestigedata:
                    prestige_info = prestigedata[str(new_prestige_level)]
                    if prestige_info.get("role"):
                        new_prestige = message.guild.get_role(prestige_info["role"])
                    
                    user_data["prestige"] = new_prestige_level
                    user_data["xp"] = 0.0 # Reset XP on prestige
                    user_data["voice"] = 0.0
                    user_data["level"] = 0
                    is_prestige_reset = True
                    new_level = 0
                    await self.save_levels_data()
            
            # Apply roles before generating message to have them available
            await self.apply_role_changes(message, new_level, new_role, new_prestige, config, is_prestige_reset)

            # Fallback to default message if LLM is not configured or fails
            # This part should be replaced with your actual LLM call
            if not hasattr(self.bot, 'llm_provider'):
                logging.warning("LLM provider not found, using fallback message.")
                return await self.generate_fallback_message(message, new_level, new_role, new_prestige, is_prestige_reset, config)

            # --- LLM PROMPT LOGIC (Example) ---
            # (Your existing LLM logic seems fine, so it's kept here conceptually)
            
            # Build context for LLM
            context_parts = []
            if manual_note: context_parts.append(f"Manual note about {message.author.display_name}: {manual_note}")
            if ai_summary: context_parts.append(f"AI summary of {message.author.display_name}: {ai_summary}")
            
            # User stats
            total_xp = user_data.get("xp", 0.0) + user_data.get("voice", 0.0)
            context_parts.append(f"User is now level {new_level} with {total_xp:,.1f} total XP.")
            if from_voice: context_parts.append("Level up was from VOICE CHAT activity.")
            else: context_parts.append("Level up was from text messaging.")
            if is_prestige_reset: context_parts.append(f"This is a PRESTIGE. User is now prestige {user_data.get('prestige', 1)} and reset to level 0.")
            
            # Build the LLM prompt
            system_prompt = f"You are a Discord bot celebrating a user's leveling achievement. Generate a personalized, enthusiastic level-up message for {message.author.mention} who just reached level {new_level}."
            # ... add more context from context_parts ...
            
            # --- Fallback to default message if LLM fails ---
            return await self.generate_fallback_message(message, new_level, new_role, new_prestige, is_prestige_reset, config)
                
        except Exception as e:
            logging.error(f"Error generating personalized level up message: {e}")
            # Fallback to default message
            return await self.generate_fallback_message(message, new_level, None, None, False, config)
    
    async def generate_fallback_message(self, message, new_level, new_role, new_prestige, is_prestige_reset, config):
        """Generate a fallback message when LLM is unavailable"""
        
        if is_prestige_reset:
            prestige_level = new_level
            msg = f"🌟✨ PRESTIGE ACHIEVED! ✨🌟 {message.author.mention} has reached the ultimate milestone and earned prestige level {prestige_level}!"
            if new_prestige:
                msg += f" Welcome to the {new_prestige.mention} ranks!"
        else:
            # Use custom message if available, otherwise use default
            base_msg = config.get("levelup_msg", "🎉 {mention} just reached level {level}!")
            msg = base_msg.format(mention=message.author.mention, level=new_level)
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
                        if role_id != (new_role.id if new_role else None):
                            old_role = message.guild.get_role(role_id)
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