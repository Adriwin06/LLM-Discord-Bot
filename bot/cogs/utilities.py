# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/cogs/utilities.py
import discord
from discord.ext import commands
from discord import app_commands
import logging
from typing import List, Optional, Union, Any, Dict
from datetime import datetime


class GenericPaginationView(discord.ui.View):
    """
    A generic pagination view that can be used for any content type.
    Extracted and generalized from the existing SummaryPaginationView.
    """
    
    def __init__(self, 
                 pages: List[str], 
                 title: str = "Paginated Content",
                 color: discord.Color = discord.Color.blue(),
                 timeout: float = 300.0,
                 ephemeral: bool = True):
        """
        Initialize the pagination view.
        
        Args:
            pages: List of strings representing each page content
            title: The title for the embed
            color: The color for the embed
            timeout: Timeout in seconds for the view
            ephemeral: Whether the pagination should be ephemeral
        """
        super().__init__(timeout=timeout)
        self.pages = pages
        self.title = title
        self.color = color
        self.current_page = 0
        self.ephemeral = ephemeral
        self.update_buttons()

    def update_buttons(self):
        """Update button states based on current page."""
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= len(self.pages) - 1
        self.page_label.label = f"Page {self.current_page + 1}/{len(self.pages)}"

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, disabled=True)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle previous page button click."""
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        await self.update_embed(interaction)

    @discord.ui.button(label="Page 1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def page_label(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Page label - not clickable."""
        pass

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle next page button click."""
        self.current_page = min(len(self.pages) - 1, self.current_page + 1)
        self.update_buttons()
        await self.update_embed(interaction)

    async def update_embed(self, interaction: discord.Interaction):
        """Update the embed with current page content."""
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    def create_embed(self) -> discord.Embed:
        """Create embed for current page."""
        embed = discord.Embed(
            title=self.title,
            description=self.pages[self.current_page] if self.current_page < len(self.pages) else "",
            color=self.color
        )
        
        if len(self.pages) > 1:
            embed.set_footer(text=f"Page {self.current_page + 1} of {len(self.pages)}")
        
        return embed


class AdvancedPaginationView(discord.ui.View):
    """
    An advanced pagination view with additional features like field-based content.
    Extracted and enhanced from the existing SummaryPaginationView.
    """
    
    def __init__(self, 
                 content: Union[List[str], List[Dict[str, Any]]], 
                 title: str,
                 color: discord.Color = discord.Color.blue(),
                 timeout: float = 300.0,
                 max_chars_per_page: int = 1000,
                 metadata: Optional[Dict[str, Any]] = None):
        """
        Initialize advanced pagination view.
        
        Args:
            content: Either list of strings or list of dicts with embed field data
            title: Title for the embed
            color: Color for the embed
            timeout: Timeout in seconds
            max_chars_per_page: Maximum characters per page for automatic splitting
            metadata: Additional metadata to display in fields
        """
        super().__init__(timeout=timeout)
        self.title = title
        self.color = color
        self.metadata = metadata or {}
        self.current_page = 0
        
        # Process content into pages
        if isinstance(content, list) and content and isinstance(content[0], str):
            # Handle string content - split into pages
            self.pages = self._split_string_content(content[0] if len(content) == 1 else '\n'.join(content), max_chars_per_page)
            self.content_type = "string"
        elif isinstance(content, list) and content and isinstance(content[0], dict):
            # Handle structured content
            self.pages = content
            self.content_type = "structured"
        else:
            # Fallback
            self.pages = [str(content)]
            self.content_type = "string"
            
        self.update_buttons()

    def _split_string_content(self, content: str, max_chars: int) -> List[str]:
        """Split string content into pages preserving formatting."""
        if len(content) <= max_chars:
            return [content]
        
        pages = []
        lines = content.split('\n')
        current_page = ""
        
        for line in lines:
            # Check if adding this line would exceed the limit
            if len(current_page) + len(line) + 1 > max_chars:
                if current_page:
                    pages.append(current_page.rstrip())
                    current_page = line
                else:
                    # Line itself is too long, split it
                    if len(line) > max_chars:
                        for i in range(0, len(line), max_chars):
                            chunk = line[i:i+max_chars]
                            if i == 0 and current_page:
                                current_page += "\n" + chunk
                            else:
                                if current_page:
                                    pages.append(current_page.rstrip())
                                current_page = chunk
                    else:
                        current_page = line
            else:
                if current_page:
                    current_page += "\n" + line
                else:
                    current_page = line
        
        if current_page:
            pages.append(current_page.rstrip())
        
        return pages

    def update_buttons(self):
        """Update button states."""
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
        pass

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(len(self.pages) - 1, self.current_page + 1)
        self.update_buttons()
        await self.update_embed(interaction)

    async def update_embed(self, interaction: discord.Interaction):
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    def create_embed(self) -> discord.Embed:
        """Create embed for current page."""
        embed = discord.Embed(title=self.title, color=self.color)
        
        if self.content_type == "string":
            # String content goes in description
            page_text = self.pages[self.current_page] if self.current_page < len(self.pages) else ""
            embed.description = page_text
        else:
            # Structured content
            page_data = self.pages[self.current_page] if self.current_page < len(self.pages) else {}
            if isinstance(page_data, dict):
                embed.description = page_data.get("description", "")
                
                # Add fields if present
                fields = page_data.get("fields", [])
                for field in fields:
                    embed.add_field(
                        name=field.get("name", "Field"),
                        value=field.get("value", "No value"),
                        inline=field.get("inline", False)
                    )

                thumbnail_url = page_data.get("thumbnail_url")
                if thumbnail_url:
                    embed.set_thumbnail(url=thumbnail_url)

                image_url = page_data.get("image_url")
                if image_url:
                    embed.set_image(url=image_url)
        
        # Add metadata fields
        for key, value in self.metadata.items():
            embed.add_field(name=key, value=str(value), inline=True)
        
        if len(self.pages) > 1:
            embed.set_footer(text=f"Page {self.current_page + 1} of {len(self.pages)}")
        
        return embed

    @staticmethod
    async def send_paginated_text(
        interaction: discord.Interaction,
        content: str,
        title: str,
        color: discord.Color = discord.Color.blue(),
        ephemeral: bool = False,
        max_chars_per_page: int = 1800,
        use_embed_for_single: bool = True,
        fields: Optional[List[Dict[str, Any]]] = None,
        thumbnail_url: Optional[str] = None,
        image_url: Optional[str] = None
    ) -> Optional[discord.Message]:
        safe_content = content or "No content."
        pages = MessageChunker.split_content(safe_content, max_length=max_chars_per_page)
        if not pages:
            pages = ["No content."]

        if len(pages) == 1:
            if not use_embed_for_single and not fields and not thumbnail_url and not image_url:
                return await AdvancedPaginationView._send_interaction_message(
                    interaction,
                    content=pages[0],
                    ephemeral=ephemeral
                )

            embed = discord.Embed(title=title, description=pages[0], color=color)
            if thumbnail_url:
                embed.set_thumbnail(url=thumbnail_url)
            if image_url:
                embed.set_image(url=image_url)
            if fields:
                for field in fields:
                    embed.add_field(
                        name=field.get("name", "Field"),
                        value=field.get("value", "No value"),
                        inline=field.get("inline", False)
                    )
            return await AdvancedPaginationView._send_interaction_message(
                interaction,
                embed=embed,
                ephemeral=ephemeral
            )

        page_entries = []
        for page in pages:
            entry = {"description": page}
            if fields:
                entry["fields"] = fields
            if thumbnail_url:
                entry["thumbnail_url"] = thumbnail_url
            if image_url:
                entry["image_url"] = image_url
            page_entries.append(entry)

        view = AdvancedPaginationView(content=page_entries, title=title, color=color)
        embed = view.create_embed()
        return await AdvancedPaginationView._send_interaction_message(
            interaction,
            embed=embed,
            view=view,
            ephemeral=ephemeral
        )

    @staticmethod
    async def _send_interaction_message(
        interaction: discord.Interaction,
        content: Optional[str] = None,
        embed: Optional[discord.Embed] = None,
        view: Optional[discord.ui.View] = None,
        ephemeral: bool = False
    ) -> Optional[discord.Message]:
        send_kwargs: Dict[str, Any] = {"ephemeral": ephemeral}
        if content is not None:
            send_kwargs["content"] = content
        if embed is not None:
            send_kwargs["embed"] = embed
        if view is not None:
            send_kwargs["view"] = view

        if interaction.response.is_done():
            return await interaction.followup.send(**send_kwargs)
        return await interaction.response.send_message(**send_kwargs)


class MessageChunker:
    """
    Utility class for splitting long messages into Discord-compatible chunks.
    Extracted and enhanced from the existing message chunking functionality.
    """
    
    @staticmethod
    def split_content(content: str, max_length: int = 1950, preserve_formatting: bool = True) -> List[str]:
        """
        Split content into chunks at natural break points.
        
        Args:
            content: The content to split
            max_length: Maximum length per chunk (default 1950 to leave room for indicators)
            preserve_formatting: Whether to try to preserve formatting at split points
            
        Returns:
            List of content chunks
        """
        if len(content) <= max_length:
            return [content]
        
        chunks = []
        remaining = content
        
        while remaining:
            if len(remaining) <= max_length:
                chunks.append(remaining)
                break
            
            # Take a slice of max_length
            chunk = remaining[:max_length]
            split_pos = max_length
            
            if preserve_formatting:
                # Try to split at paragraph break
                last_paragraph = chunk.rfind('\n\n')
                if last_paragraph > max_length // 2:
                    split_pos = last_paragraph + 2
                else:
                    # Try to split at sentence end
                    sentence_ends = [chunk.rfind('. '), chunk.rfind('! '), chunk.rfind('? ')]
                    last_sentence = max(sentence_ends)
                    if last_sentence > max_length // 2:
                        split_pos = last_sentence + 2
                    else:
                        # Try to split at word boundary
                        last_space = chunk.rfind(' ')
                        if last_space > max_length * 0.7:
                            split_pos = last_space + 1
            
            # Extract the chunk and update remaining content
            final_chunk = remaining[:split_pos].rstrip()
            if final_chunk:
                chunks.append(final_chunk)
            
            remaining = remaining[split_pos:].lstrip()
        
        return chunks
    
    @staticmethod
    async def send_chunked_message(
        target: Union[discord.abc.Messageable, discord.Interaction],
        content: str,
        max_chunks: int = 50,
        reply_to: Optional[discord.Message] = None,
        ephemeral: bool = False
    ) -> List[discord.Message]:
        """
        Send a long message as multiple chunks.
        
        Args:
            target: Where to send the message (channel, interaction, etc.)
            content: The content to send
            max_chunks: Maximum number of chunks to prevent spam
            reply_to: Message to reply to (only for first chunk)
            ephemeral: Whether the message should be ephemeral (for interactions)
            
        Returns:
            List of sent messages
        """
        # Safety check for extremely long messages
        if len(content) > 500000:  # 500KB limit
            logging.warning(f"Content is extremely long ({len(content)} chars), truncating")
            content = content[:500000] + "\n\n... [Content truncated due to extreme length]"
        
        # Split content into chunks
        chunks = MessageChunker.split_content(content)
        
        # Apply chunk limit
        if len(chunks) > max_chunks:
            logging.warning(f"Too many chunks ({len(chunks)}), limiting to {max_chunks}")
            chunks = chunks[:max_chunks]
            chunks[-1] += "\n\n... [Content truncated - too many chunks]"
        
        sent_messages = []
        
        try:
            # Send first chunk
            first_chunk = chunks[0]
            if len(chunks) > 1:
                chunk_indicator = f" (1/{len(chunks)})"
                if len(first_chunk) + len(chunk_indicator) <= 2000:
                    first_chunk += chunk_indicator
            
            # Handle different target types
            if isinstance(target, discord.Interaction):
                if target.response.is_done():
                    msg = await target.followup.send(first_chunk, ephemeral=ephemeral)
                else:
                    await target.response.send_message(first_chunk, ephemeral=ephemeral)
                    msg = await target.original_response()
                sent_messages.append(msg)
            elif reply_to:
                msg = await reply_to.reply(first_chunk)
                sent_messages.append(msg)
            else:
                msg = await target.send(first_chunk)
                sent_messages.append(msg)
            
            # Send remaining chunks
            for i, chunk in enumerate(chunks[1:], 2):
                chunk_indicator = f" ({i}/{len(chunks)})"
                if len(chunk) + len(chunk_indicator) <= 2000:
                    chunk += chunk_indicator
                
                if isinstance(target, discord.Interaction):
                    msg = await target.followup.send(chunk, ephemeral=ephemeral)
                else:
                    msg = await target.send(chunk)
                sent_messages.append(msg)
                
        except Exception as e:
            logging.error(f"Error in send_chunked_message: {e}")
            # Fallback: send truncated content
            try:
                fallback_content = content[:1900] + "\n\n... [Error occurred, content truncated]"
                if isinstance(target, discord.Interaction):
                    if not target.response.is_done():
                        await target.response.send_message(fallback_content, ephemeral=ephemeral)
                    else:
                        await target.followup.send(fallback_content, ephemeral=ephemeral)
                elif reply_to:
                    await reply_to.reply(fallback_content)
                else:
                    await target.send(fallback_content)
            except Exception as fallback_error:
                logging.error(f"Fallback also failed: {fallback_error}")
        
        return sent_messages


class EmbedBuilder:
    """
    Utility class for building Discord embeds with common patterns.
    """
    
    @staticmethod
    def create_error_embed(
        title: str = "Error",
        description: str = "An error occurred",
        color: discord.Color = discord.Color.red()
    ) -> discord.Embed:
        """Create a standard error embed."""
        return discord.Embed(
            title=f"❌ {title}",
            description=description,
            color=color
        )
    
    @staticmethod
    def create_success_embed(
        title: str = "Success",
        description: str = "Operation completed successfully",
        color: discord.Color = discord.Color.green()
    ) -> discord.Embed:
        """Create a standard success embed."""
        return discord.Embed(
            title=f"✅ {title}",
            description=description,
            color=color
        )
    
    @staticmethod
    def create_info_embed(
        title: str = "Information",
        description: str = "",
        color: discord.Color = discord.Color.blue()
    ) -> discord.Embed:
        """Create a standard info embed."""
        return discord.Embed(
            title=f"ℹ️ {title}",
            description=description,
            color=color
        )
    
    @staticmethod
    def create_warning_embed(
        title: str = "Warning",
        description: str = "",
        color: discord.Color = discord.Color.orange()
    ) -> discord.Embed:
        """Create a standard warning embed."""
        return discord.Embed(
            title=f"⚠️ {title}",
            description=description,
            color=color
        )


class UtilityHelpers:
    """
    Collection of utility helper functions.
    """
    
    @staticmethod
    def format_time_duration(seconds: int) -> str:
        """
        Format a duration in seconds to a human-readable string.
        
        Args:
            seconds: Duration in seconds
            
        Returns:
            Formatted string like "2h 30m 15s"
        """
        if seconds < 60:
            return f"{seconds}s"
        
        minutes = seconds // 60
        seconds = seconds % 60
        
        if minutes < 60:
            return f"{minutes}m {seconds}s"
        
        hours = minutes // 60
        minutes = minutes % 60
        
        if hours < 24:
            if seconds > 0:
                return f"{hours}h {minutes}m {seconds}s"
            else:
                return f"{hours}h {minutes}m"
        
        days = hours // 24
        hours = hours % 24
        
        if minutes > 0 or seconds > 0:
            return f"{days}d {hours}h {minutes}m"
        else:
            return f"{days}d {hours}h"
    
    @staticmethod
    def format_timestamp(dt: datetime, style: str = "R") -> str:
        """
        Format a datetime as a Discord timestamp.
        
        Args:
            dt: The datetime to format
            style: Discord timestamp style (R=relative, F=full, etc.)
            
        Returns:
            Discord timestamp string
        """
        timestamp = int(dt.timestamp())
        return f"<t:{timestamp}:{style}>"
    
    @staticmethod
    def safe_username(user: Union[discord.User, discord.Member]) -> str:
        """
        Get a safe username that handles display names and usernames.
        
        Args:
            user: Discord user or member
            
        Returns:
            Safe username string
        """
        if hasattr(user, 'display_name'):
            return user.display_name
        elif hasattr(user, 'global_name') and user.global_name:
            return user.global_name
        else:
            return user.name
    
    @staticmethod
    def truncate_string(text: str, max_length: int, suffix: str = "...") -> str:
        """
        Truncate a string to a maximum length with suffix.
        
        Args:
            text: Text to truncate
            max_length: Maximum length including suffix
            suffix: Suffix to add when truncating
            
        Returns:
            Truncated string
        """
        if len(text) <= max_length:
            return text
        
        return text[:max_length - len(suffix)] + suffix
    
    @staticmethod
    def format_number(number: Union[int, float], precision: int = 1) -> str:
        """
        Format a number with K/M/B suffixes.
        
        Args:
            number: Number to format
            precision: Decimal places to show
            
        Returns:
            Formatted number string
        """
        if abs(number) >= 1_000_000_000:
            return f"{number / 1_000_000_000:.{precision}f}B"
        elif abs(number) >= 1_000_000:
            return f"{number / 1_000_000:.{precision}f}M"
        elif abs(number) >= 1_000:
            return f"{number / 1_000:.{precision}f}K"
        else:
            if isinstance(number, float):
                return f"{number:.{precision}f}"
            else:
                return str(number)


class Utilities(commands.Cog):
    """
    Shared utility functions for the bot including pagination, message chunking, and helpers.
    This cog provides reusable utilities that can be imported by other cogs.
    """
    
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name="test_pagination", description="Test the pagination system (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def test_pagination(self, interaction: discord.Interaction):
        """Test command to demonstrate pagination functionality."""
        await interaction.response.defer(ephemeral=True)
        
        # Create test content
        test_content = []
        for i in range(5):
            page_content = f"**Page {i+1} Content**\n\n"
            page_content += f"This is test page {i+1} with some content to demonstrate pagination.\n"
            page_content += "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 10
            test_content.append(page_content)
        
        # Create pagination view
        view = GenericPaginationView(
            pages=test_content,
            title="Test Pagination",
            color=discord.Color.purple()
        )
        
        embed = view.create_embed()
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    
    @app_commands.command(name="test_chunking", description="Test the message chunking system (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def test_chunking(self, interaction: discord.Interaction):
        """Test command to demonstrate message chunking functionality."""
        await interaction.response.defer(ephemeral=True)
        
        # Create test content that's longer than Discord's limit
        test_content = "This is a test of the message chunking system.\n\n"
        test_content += "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 100
        test_content += "\n\nThis content is intentionally long to demonstrate how the chunking system "
        test_content += "splits messages at natural break points while preserving formatting. " * 50
        
        # Send paginated message
        await AdvancedPaginationView.send_paginated_text(
            interaction=interaction,
            content=test_content,
            title="Test Chunking",
            color=discord.Color.purple(),
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(Utilities(bot))
