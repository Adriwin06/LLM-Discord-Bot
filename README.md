# LLM-Powered Discord Bot

This is a sophisticated, LLM-powered Discord bot built with Python, `discord.py`, and `litellm`. It's designed to be highly modular, configurable, and feature-rich, providing intelligent and context-aware interactions in your Discord server.

## Features

- **Universal LLM Support**: Thanks to `litellm`, the bot can connect to over 100 different LLM providers (OpenAI, Gemini, Anthropic, etc.).
- **Dual-LLM System**: Uses a powerful model for generating high-quality responses and a separate, lightweight model for quick, cost-effective decisions on whether to reply or react.
- **Rich Context Awareness**:
    - **Channel Summaries**: Automatically creates and maintains evolving summaries of channel conversations.
    - **User Profiles**: Generates AI-powered summaries of user personalities and interests, which can be supplemented with manual notes.
    - **Dynamic History**: Fetches recent messages and follows reply chains to understand the immediate context of a conversation.
    - **Server Emojis**: Provides the LLM with custom server emoji formats so it can use them in replies and reactions.
- **Advanced Media Handling**:
    - Processes images, audio, video, PDFs, and Office documents.
    - Conditionally uses vision, audio, and document-processing capabilities based on the configured LLM.
    - Transcribes audio locally with `faster-whisper`, extracts representative video frames/audio, performs OCR on images, and extracts text from documents as a fallback for text-only models.
- **Intelligent Interactions**:
    - **AI-Driven Replies & Reactions**: Decides when to reply, when to react with an emoji, or when to stay silent.
    - **Mention Resolver**: Allows the LLM to naturally ping users and roles using a safe, permission-aware syntax (`<mention user="Name">`).
    - **Web Search**: Can perform web searches to answer questions with up-to-date information if the model supports it.
- **Robust Administration**:
    - **Slash Commands**: Full configuration via intuitive slash commands.
    - **Per-Channel Overrides**: Customize prompts, models, and behavior for specific channels.
    - **Data Persistence**: Cleanly separates server settings (`settings.json`) from dynamic data like summaries and profiles (`data.json`).
- **Asynchronous & Scalable**: Built entirely on `asyncio` for high performance and concurrency.
- **Automatic Backups**: Periodically backs up critical data files.

## Setup

### 1. Clone the Repository

```bash
git clone https://github.com/your-username/LLM-Discord-Bot.git
cd LLM-Discord-Bot
```

### 2. Create a Virtual Environment

It's highly recommended to use a virtual environment to manage dependencies.

**Windows:**
```bash
python -m venv venv
.\venv\Scripts\activate
```

**macOS/Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

Install all the required Python packages using pip.

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

The bot is configured using a `.env` file. Create one by copying the example file:

```bash
# On Windows
copy .env.example .env

# On macOS/Linux
cp .env.example .env
```

Now, open the `.env` file in a text editor and fill in the required values:

-   `DISCORD_TOKEN`: Your Discord bot's token. You can get this from the [Discord Developer Portal](https://discord.com/developers/applications).
-   `MAIN_LLM_MODEL` & `DECISION_LLM_MODEL`: The model strings for your chosen LLM provider (e.g., `gpt-4o`, `gemini/gemini-1.5-pro-latest`).
-   `DECISION_LLM_ENABLED`: Set to `False` to disable ambient reply/reaction decisions; the bot will only answer direct mentions/replies.
-   **API Keys**: Add the API keys for the LLM providers you intend to use. LiteLLM reads standard provider env vars directly, such as `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, or `MISTRAL_API_KEY`.
-   `REPLY_CHAIN_DEBOUNCE_SECONDS`: How long the bot waits for same-user message fragments before deciding whether to reply. Defaults to `2.0`.
-   `REPLY_CHAIN_WAIT_FOR_TYPING`, `REPLY_CHAIN_TYPING_MAX_WAIT_SECONDS`, and `REPLY_CHAIN_LONG_TYPING_SECONDS`: Let the bot wait while the user is still typing, with a max wait and optional long-typing joke/GIF decision.
-   `GIFS_ENABLED`: Lets the decision model choose from a small curated GIF set when a GIF is funnier or more logical than text.
-   `WEB_SEARCH_CONTEXT_SIZE`: Controls the amount of web search context (options: `low`, `medium`, `high`). Only applies to models that support web search like `openai/gpt-4o-search-preview`, `gemini/gemini-2.0-flash`, etc.
-   `LOCAL_STT_MODEL`, `LOCAL_STT_DEVICE`, and `LOCAL_STT_COMPUTE_TYPE`: Configure local speech-to-text for audio and video attachments. Defaults are CPU-friendly (`base`, `cpu`, `int8`); use `cuda`/`float16` if you have a compatible GPU. `faster-whisper` may download the selected model on first use unless `LOCAL_STT_MODEL` points at a local model path.
-   Customize other settings like the behavior prompt, rate limits, and backup interval as needed.

### 5. Run the Bot

Once configured, you can start the bot:

```bash
python main.py
```

If everything is set up correctly, you will see a confirmation message in your console, and the bot will appear online in your Discord server.

## Usage

### Administration

All administrative tasks are handled via slash commands. You must have administrative permissions on the server to use these.

-   `/llm settings`: Configure the primary LLM model, behavior prompt, and summary triggers for the entire server.
-   `/llm decision`: View or toggle the decision model. Disabling it makes the bot answer only direct mentions/replies.
-   `/channel override`: Set channel-specific models, prompts, and settings that override the server-wide configuration.
-   `/context reset`: Clear the bot's conversational context for a channel or the entire server.
-   `/note`: Manage user profiles.
    -   `/note add`: Add a manual note to a user's profile.
    -   `/note view`: See a user's AI-generated summary and manual note.
    -   `/note refresh ai`: Force the bot to regenerate a user's AI summary based on their recent messages.
-   `/backup`: Manually trigger a backup of the bot's data.

### Fun Commands

-   `/fun insult @user`: The bot will generate a personalized insult for the mentioned user.
-   `/fun compliment @user`: The bot will generate a personalized compliment for the mentioned user.
-   `/mock message @user`: The bot will mock the user's last message in "sPoNgEbOb TeXt" format.

## Project Structure

The codebase is organized into a modular structure for clarity and maintainability:

-   `main.py`: Main entry point. Initializes and runs the bot.
-   `requirements.txt`: A list of all the Python packages required.
-   `.env.example`: An example file for environment variables.
-   `/bot`: Core bot logic.
    -   `config.py`: Loads and manages configuration from environment variables.
    -   `store.py`: Handles thread-safe reading and writing to `settings.json` and `data.json`.
    -   `llm_provider.py`: The central class for all interactions with `litellm`.
    -   `context_manager.py`: Manages building context for the LLM (history, summaries, profiles).
    -   `/cogs`: Command modules (Cogs).
        -   `admin_commands.py`: Implements all administrative slash commands.
        -   `profile_commands.py`: Implements the `/note` command suite.
        -   `fun_commands.py`: Implements fun commands like `/insult` and `/mock`.
        -   `event_handler.py`: Handles the `on_message` event for AI reply/reaction logic.
-   `/data`: Stores persistent data.
    -   `settings.json`: Server configurations set by admins.
    -   `data.json`: Dynamic data like channel summaries and user profiles.
-   `/.backup`: Stores timestamped backups of the data files.
-   `/.vscode`: Contains VS Code-specific settings, like `launch.json` for easy debugging.

## Contributing

Contributions are welcome! Please feel free to submit a pull request or open an issue for any bugs or feature requests.
