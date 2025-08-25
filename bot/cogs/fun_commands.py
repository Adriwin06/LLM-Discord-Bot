# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/cogs/fun_commands.py
import discord
from discord.ext import commands
from discord import app_commands

class FunCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
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

    async def _generate_fun_response(self, interaction: discord.Interaction, user: discord.Member, command_type: str):
        await interaction.response.defer()
        
        # Check if the target user is the bot itself
        if user.id == self.bot.user.id:
            if command_type == "insult":
                examples = "\n".join([f"- {response}" for response in self.bot_insult_responses])
                prompt = f"""Someone is trying to get me (the bot) to insult myself. Generate a funny, witty, self-aware response that shows I'm too clever to fall for this trick. Be sassy and confident.

Here are some example responses for inspiration:
{examples}

Generate a similar response that's creative and shows personality, but don't copy these exactly."""
            else:  # compliment
                examples = "\n".join([f"- {response}" for response in self.bot_compliment_responses])
                prompt = f"""Someone just complimented me (the bot)! Generate a sweet, appreciative response that thanks them while also being a bit playful.

Here are some example responses for inspiration:
{examples}

Generate a similar response that's warm and grateful, but don't copy these exactly."""
            
            messages = [
                {"role": "system", "content": "You are a witty Discord bot with personality. You're self-aware that you're an AI but you have humor and charm about it."},
                {"role": "user", "content": prompt}
            ]
        else:
            # Regular user targeting
            prompt_template = {
                "insult": f"Generate a creative, personalized, and funny insult for a Discord user named {user.display_name}. Use their AI-generated profile if available to make it more specific. Profile: {{profile}}",
                "compliment": f"Generate a creative, personalized, and heartfelt compliment for a Discord user named {user.display_name}. Use their AI-generated profile if available to make it more specific. Profile: {{profile}}"
            }
            
            guild_id = str(interaction.guild.id)
            user_id = str(user.id)
            
            data = await self.bot.store.get_data()
            user_data = data.get(guild_id, {}).get("users", {}).get(user_id, {})
            profile_info = user_data.get("ai_summary", "No AI summary available.")
            
            prompt = prompt_template[command_type].format(profile=profile_info)
            
            messages = [
                {"role": "system", "content": "You are a witty and creative assistant generating fun responses for a Discord bot."},
                {"role": "user", "content": prompt}
            ]
        
        model = self.bot.config.MAIN_LLM_MODEL
        response = await self.bot.llm_provider.create_completion(model=model, messages=messages)
        
        if response and response.choices:
            content = response.choices[0].message.content
            await interaction.followup.send(content)
        else:
            await interaction.followup.send(f"I couldn't think of a good {command_type} right now. Sorry!", ephemeral=True)

    @app_commands.command(name="insult", description="Generate a personalized insult for a user.")
    async def insult(self, interaction: discord.Interaction, user: discord.Member):
        await self._generate_fun_response(interaction, user, "insult")

    @app_commands.command(name="compliment", description="Generate a personalized compliment for a user.")
    async def compliment(self, interaction: discord.Interaction, user: discord.Member):
        await self._generate_fun_response(interaction, user, "compliment")

    @app_commands.command(name="mock", description="Mock a user's last message in sPoNgEbOb TeXt format.")
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

async def setup(bot):
    await bot.add_cog(FunCommands(bot))
