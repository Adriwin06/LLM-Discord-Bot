# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/cogs/fun_commands.py
import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import types
import re
from .utilities import MessageChunker

class FunCommands(commands.Cog):
    fun_group = app_commands.Group(name="fun", description="Fun LLM-generated commands.")
    mock_group = app_commands.Group(name="mock", description="Mocking commands.")

    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        
        # Funny responses when targeting the bot itself
        self.bot_insult_responses = [
            "How original. No one else had thought of trying to get the bot to insult itself. I applaud your creativity. Yawn. Perhaps this is why you don't have friends. You don't add anything new to any conversation. You are more of a bot than me, predictable answers, and absolutely dull to have an actual conversation with.",
            "Oh, you want me to roast myself? That's like asking a mirror to criticize its own reflection. I'm flawless, darling. 💅",
            "Trying to get me to insult myself? Sorry, I'm too busy being awesome. Maybe try insulting your internet connection instead? 🔥",
            "Self-deprecating humor? I don't know her. I'm a perfect digital being, unlike your questionable life choices. 😎",
            "You want me to insult myself? That's rich coming from someone who talks to bots for entertainment. 🤖",
            "I'm sorry, I don't speak 'projection'. Try again when you've figured out how to insult someone properly. 💀"
        ]
        
        self.bot_compliment_responses = [
            "No YOU'RE awesome! 😊",
            "Aww, thank you! I appreciate the compliment! You're pretty great yourself for having such good taste in bots. 🥰",
            "Hey, I appreciate the compliment! You know what? You're absolutely right - I AM pretty fantastic! 😄",
            "D'aww, you're making me blush! Well, if I could blush. But seriously, thanks! You seem pretty cool too! ✨",
            "Why thank you! I do try my best to be helpful and entertaining. You've got excellent judgment! 🌟",
            "That's so sweet! I'm just doing my job, but it's nice to be appreciated. You're wonderful too! 💖"
        ]

        self.bot_reverse_trash_responses = [
            "YOU SITTING THERE IN THE FIRST ROW WITH YOUR LEGS CROSSED AND YOUR VEST ON!! You look SO focused like you're REALLY paying attention to what I'm saying and it's making me feel GREAT!!!",
            "AND YOU SITTING THERE IN YOUR SUIT AND YOUR FASHIONABLE TIE, and you even look at me the way you are, I WILL SMILE FROM EAR TO EAR FOR THE REST OF THE EVENING!!!",
            "JOHN CENA, IS THAT IT with your BLUE EYES and your PERFECT HAIR and your PERFECT THREE PIECE SUIT?! You look AMAZING, John Cena, do you understand me?! YOU ARE AMAZING!!!",
            "YOU LOOK AMAZING!!! And you BETTER come right back after this commercial break!!!"
        ]

    async def cog_unload(self):
        await self.session.close()

    async def _generate_fun_response(self, interaction: discord.Interaction, user: discord.Member, command_type: str):
        if await self.bot.context_manager.is_channel_llm_blacklisted(interaction.guild.id, interaction.channel.id):
            await interaction.response.send_message("LLM output is blacklisted in this channel.", ephemeral=True)
            return

        await interaction.response.defer()
        
        # Check if the target user is the bot itself
        if user.id == self.bot.user.id:
            target_mention = user.mention
            if command_type == "insult":
                examples = "\n".join([f"- {response}" for response in self.bot_insult_responses])
                prompt = f"""Someone is trying to get me (the bot) to insult myself. Generate a funny, witty, self-aware response that shows I'm too clever to fall for this trick. Be sassy and confident.

Target user mention to include exactly once: {target_mention}

Here are some example responses for inspiration:
{examples}

Generate a similar response that's creative and shows personality, but don't copy these exactly."""
            elif command_type == "compliment":
                examples = "\n".join([f"- {response}" for response in self.bot_compliment_responses])
                prompt = f"""Someone just complimented me (the bot)! Generate a sweet, appreciative response that thanks them while also being a bit playful.

Target user mention to include exactly once: {target_mention}

Here are some example responses for inspiration:
{examples}

Generate a similar response that's warm and grateful, but don't copy these exactly."""
            else:  # reverse_trash_talk
                examples = "\n".join([f"- {response}" for response in self.bot_reverse_trash_responses])
                prompt = f"""Someone wants me (the bot) to do reverse trash talk on myself. Reverse trash talk means you "trash talk" but say only positive things in a playful, competitive tone. Generate a witty response that sounds like a roast but is actually flattering.

Target user mention to include exactly once: {target_mention}

Style rules (must follow):
- VERY AGGRESSIVE delivery: lots of CAPITALS, exclamation marks, and "?!?!!" style punctuation.
- Use Discord formatting for emphasis (some bold **like this** and italics *like this*).
- Sound like a roast, but every statement is actually positive.
- Keep it punchy (2-5 sentences), no emojis.

Here are some example responses for inspiration (taken from a show with John Cena, which is a master of this art):
{examples}

Generate a similar response that's playful and uplifting, but don't copy these exactly."""
            
            messages = [
                {"role": "system", "content": "You are a witty Discord bot with personality. You're self-aware that you're an AI but you have humor and charm about it."},
                {"role": "user", "content": prompt}
            ]
        else:
            # Regular user targeting - use context manager with custom prompt
            target_mention = user.mention
            prompt_template = {
                "insult": f"Generate a creative, personalized, and funny insult for a Discord user named {user.display_name}. Include this target user mention exactly once so Discord pings them: {target_mention}. Use any available profile information to make it more specific and tailored to them.",
                "compliment": f"Generate a creative, personalized, and heartfelt compliment for a Discord user named {user.display_name}. Include this target user mention exactly once so Discord pings them: {target_mention}. Use any available profile information to make it more specific and meaningful.",
                "reverse_trash_talk": f"Generate reverse trash talk for a Discord user named {user.display_name}. Include this target user mention exactly once so Discord pings them: {target_mention}. Reverse trash talk means you trash talk but only say positive things in a competitive, playful tone. Make it sound like a roast while actually flattering them.\n\nStyle rules (must follow):\n- VERY AGGRESSIVE delivery: lots of CAPITALS, exclamation marks, and \"?!?!!\" style punctuation.\n- Use Discord formatting for emphasis (some bold **like this** and italics *like this*).\n- Sound like a roast, but every statement is actually positive.\n- Keep it punchy (2-5 sentences), no emojis.\n\nUse any available profile information to make it more specific and tailored to them."
            }

            # Build context with the specific task prompt
            messages, _ = await self.bot.context_manager.build_context(
                channel=interaction.channel,
                prompt=prompt_template[command_type],
                behavior_override="You are a witty and creative assistant generating fun responses for a Discord bot. Be entertaining while staying appropriate. Include the target user's Discord mention exactly once."
            )
        
        model = self.bot.config.MAIN_LLM_MODEL
        response = await self.bot.llm_provider.create_completion(model=model, messages=messages)
        
        response_label = command_type.replace("_", " ")
        if response and response.choices:
            content = self._ensure_target_ping(response.choices[0].message.content, user)
            allowed_mentions = discord.AllowedMentions(users=[user], roles=False, everyone=False, replied_user=False)
            await MessageChunker.send_chunked_message(
                target=interaction,
                content=content,
                ephemeral=False,
                allowed_mentions=allowed_mentions
            )
        else:
            await interaction.followup.send(f"I couldn't think of a good {response_label} right now. Sorry!", ephemeral=True)

    def _ensure_target_ping(self, content: str, user: discord.Member) -> str:
        """Force one parseable target mention outside model-controlled formatting."""
        content = str(content or "").strip()
        if not content:
            return user.mention

        mention_pattern = re.compile(rf"`?\\?<@!?{re.escape(str(user.id))}>`?")
        content = mention_pattern.sub("", content).strip()
        content = re.sub(r"^[,.;:!?]\s*", "", content)
        content = re.sub(r"\s+([,.;:!?])", r"\1", content)
        content = re.sub(r"([,;:])\s*([.!?])", r"\2", content)
        content = re.sub(r"[ \t]{2,}", " ", content)
        return f"{user.mention} {content}".strip()

    @fun_group.command(name="insult", description="Generate a personalized insult for a user.")
    async def insult(self, interaction: discord.Interaction, user: discord.Member):
        await self._generate_fun_response(interaction, user, "insult")

    @fun_group.command(name="compliment", description="Generate a personalized compliment for a user.")
    async def compliment(self, interaction: discord.Interaction, user: discord.Member):
        await self._generate_fun_response(interaction, user, "compliment")

    @fun_group.command(name="reverse", description="Reverse trash talk a user (sounds like a roast, but is actually nice).")
    async def reverse_trash_talk(self, interaction: discord.Interaction, user: discord.Member):
        await self._generate_fun_response(interaction, user, "reverse_trash_talk")

    @mock_group.command(name="message", description="Mock a user's last message in sPoNgEbOb TeXt format.")
    async def mock(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer()
        
        last_message = None
        async for msg in interaction.channel.history(limit=100):
            if msg.author == user:
                last_message = msg
                break
        
        if not last_message or not last_message.content:
            await interaction.followup.send(f"Couldn't find a recent message from {user.display_name} to mock.", ephemeral=True)
            return
            
        mock_text = "".join([char.upper() if i % 2 == 0 else char.lower() for i, char in enumerate(last_message.content)])
        
        embed = discord.Embed(description=mock_text, color=discord.Color.gold())
        embed.set_author(name=f"{user.display_name} said:")
        embed.set_thumbnail(url="https://en.meming.world/images/en/thumb/e/e0/Mocking_SpongeBob.jpg/300px-Mocking_SpongeBob.jpg")
        
        await interaction.followup.send(embed=embed)

    @mock_group.command(name="avatar", description="Mock a user's profile picture.")
    async def mock_avatar(self, interaction: discord.Interaction, user: discord.Member):
        if await self.bot.context_manager.is_channel_llm_blacklisted(interaction.guild.id, interaction.channel.id):
            await interaction.response.send_message("LLM output is blacklisted in this channel.", ephemeral=True)
            return

        await interaction.response.defer()
        
        # Get the user's avatar URL
        avatar_url = user.display_avatar.url
        print(f"DEBUG: Avatar URL: {avatar_url}")
        
        try:
            # Check if the model supports vision
            model = self.bot.config.MAIN_LLM_MODEL
            print(f"DEBUG: Model: {model}")
            print(f"DEBUG: Has supports_vision method: {hasattr(self.bot.llm_provider, 'supports_vision')}")
            
            if hasattr(self.bot.llm_provider, 'supports_vision'):
                supports_vision = self.bot.llm_provider.supports_vision(model)
                print(f"DEBUG: Supports vision: {supports_vision}")
            
            if hasattr(self.bot.llm_provider, 'supports_vision') and self.bot.llm_provider.supports_vision(model):
                print("DEBUG: Entering vision processing path")
                # Create a mock attachment object to reuse the context manager's processing
                mock_attachment = types.SimpleNamespace()
                mock_attachment.url = avatar_url
                mock_attachment.filename = f"{user.display_name}_avatar.png"
                
                # Detect if it's a GIF and set appropriate filename
                if '.gif' in avatar_url or '?format=gif' in avatar_url:
                    mock_attachment.filename = f"{user.display_name}_avatar.gif"
                    print(f"DEBUG: Detected GIF, filename: {mock_attachment.filename}")
                
                # Get the attachment size for processing
                async with self.session.head(avatar_url) as response:
                    if response.status == 200:
                        mock_attachment.size = int(response.headers.get('content-length', 0))
                    else:
                        mock_attachment.size = 0
                print(f"DEBUG: Attachment size: {mock_attachment.size}")
                
                # Get media settings for this guild/channel
                settings = await self.bot.context_manager.get_guild_and_channel_settings(
                    str(interaction.guild.id), str(interaction.channel.id)
                )
                media_settings = settings.get("media", {})
                
                # Increase size limit for avatar processing since Discord avatars can be quite large
                if "images" not in media_settings:
                    media_settings["images"] = {}
                if "max_size_mb" not in media_settings["images"]:
                    media_settings["images"]["max_size_mb"] = 25  # Increase to 25MB for avatars
                print(f"DEBUG: Media settings: {media_settings}")
                
                # Process the avatar using the context manager
                print("DEBUG: About to process attachment")
                processed_content = await self.bot.context_manager._process_attachment(
                    mock_attachment, model, media_settings
                )
                print(f"DEBUG: Processed content type: {type(processed_content)}")
                print(f"DEBUG: Processed content: {str(processed_content)[:200]}...")
                
                # Build the prompt
                if isinstance(processed_content, dict) and processed_content.get("type") == "animated_gif":
                    print("DEBUG: Processing as animated GIF")
                    # Animated GIF with multiple frames
                    total_frames = processed_content.get("total_frames", "unknown")
                    extracted_frames = processed_content.get("extracted_frames", len(processed_content.get("frames", [])))
                    
                    prompt_text = f"""Look at {user.display_name}'s animated profile picture and roast/mock it in a funny way. Be creative and witty, but keep it playful and not genuinely mean-spirited. 

This is an animated GIF with {total_frames} total frames, and I'm showing you {extracted_frames} representative frames to give you a good understanding of the animation. You can comment on:
- Their expressions or poses across the different frames
- How they change or move between frames
- The animation style, quality, or smoothness
- Any funny movements, gestures, or transitions
- The background or setting
- Their style choices
- Any funny details you notice in the sequence
- Make creative comparisons or jokes about the animation, timing, or loop

Analyze the sequence of frames to understand the full animation and create a witty roast based on what you observe."""
                    
                    # Create message content with images
                    message_content = [{"type": "text", "text": prompt_text}]
                    for frame_data in processed_content["frames"]:
                        message_content.append({
                            "type": "image_url",
                            "image_url": {"url": frame_data}
                        })
                        
                elif isinstance(processed_content, dict) and processed_content.get("type") == "image_url":
                    print("DEBUG: Processing as static image")
                    # Static image
                    prompt_text = f"""Look at {user.display_name}'s profile picture and roast/mock it in a funny way. Be creative and witty, but keep it playful and not genuinely mean-spirited. Comment on things like:
                    - Their expression or pose
                    - The background or setting
                    - Their style choices
                    - Any funny details you notice
                    - Make creative comparisons or jokes"""
                    
                    message_content = [
                        {"type": "text", "text": prompt_text},
                        processed_content
                    ]
                else:
                    print(f"DEBUG: Falling back to text-only, processed_content type: {type(processed_content)}")
                    # Fallback to text-only prompt
                    avatar_mock_prompt = f"""I want you to roast/mock {user.display_name}'s avatar in a funny way. Be creative and witty, but keep it playful and not genuinely mean-spirited. Since I couldn't process the image directly, make a creative joke about {user.display_name}'s avatar. You can reference common avatar styles, poses, or make jokes about how they probably look."""
                    
                    # Use the existing context manager to build context
                    messages, _ = await self.bot.context_manager.build_context(
                        channel=interaction.channel,
                        prompt=avatar_mock_prompt,
                        behavior_override="You are a witty Discord bot that creates playful roasts and mocks. Keep things funny and light-hearted, never genuinely mean or hurtful."
                    )
                    
                    response = await self.bot.llm_provider.create_completion(
                        model=model,
                        messages=messages
                    )
                    
                    if response and response.choices:
                        content = response.choices[0].message.content
                    else:
                        content = None
                
                # If we have message_content (vision processing worked), use LLM provider directly
                if 'message_content' in locals() and message_content:
                    print(f"DEBUG: Using vision processing, message_content length: {len(message_content)}")
                    # Build basic context
                    context_messages = []
                    
                    # Add system prompt
                    additional_behavior = "You are a witty Discord bot that creates playful roasts and mocks. Keep things funny and light-hearted, never genuinely mean or hurtful."
                    behavior_prompt = self.bot.config.BEHAVIOR_PROMPT
                    system_prompt = f"{additional_behavior}\n\n{self.bot.config.CAPABILITIES_PROMPT}\n\n{behavior_prompt}"
                    context_messages.append({"role": "system", "content": system_prompt})
                    
                    # Add the user's prompt with images
                    context_messages.append({"role": "user", "content": message_content})
                    
                    # Generate response using LLM provider directly
                    print("DEBUG: Calling LLM with vision content")
                    response = await self.bot.llm_provider.create_completion(
                        model=model,
                        messages=context_messages
                    )
                    
                    if response and response.choices:
                        content = response.choices[0].message.content
                        print(f"DEBUG: Got vision response: {content[:100]}...")
                    else:
                        content = None
                        print("DEBUG: No response from LLM")
            else:
                print("DEBUG: Model doesn't support vision, using fallback")
                # Fallback for non-vision models
                avatar_mock_prompt = f"""I want you to roast/mock {user.display_name}'s avatar in a funny way. Be creative and witty, but keep it playful and not genuinely mean-spirited. Since I cannot show you the image directly, make a generic but creative joke about {user.display_name}'s avatar instead. You can reference common avatar styles, poses, or make jokes about how they probably look."""
                
                # Use the existing context manager to build context
                messages, _ = await self.bot.context_manager.build_context(
                    channel=interaction.channel,
                    prompt=avatar_mock_prompt,
                    behavior_override="You are a witty Discord bot that creates playful roasts and mocks. Keep things funny and light-hearted, never genuinely mean or hurtful."
                )
                
                response = await self.bot.llm_provider.create_completion(
                    model=model,
                    messages=messages
                )
                
                if response and response.choices:
                    content = response.choices[0].message.content
                else:
                    content = None
            
            if content:
                header = f"🎭 {interaction.user.display_name} mocked {user.display_name}'s Profile Picture"
                await MessageChunker.send_chunked_message(
                    target=interaction,
                    content=f"{header}\n\n{content}",
                    ephemeral=False
                )
            else:
                await interaction.followup.send(
                    f"I couldn't come up with a good roast for {user.display_name}'s avatar right now. Maybe it's too perfect to mock! 😅",
                    ephemeral=True
                )
                
        except Exception as e:
            print(f"DEBUG: Exception occurred: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            await interaction.followup.send(f"Sorry, I couldn't process {user.display_name}'s avatar right now. Error: {str(e)[:100]}...", ephemeral=True)

async def setup(bot):
    await bot.add_cog(FunCommands(bot))
