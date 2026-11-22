# LLM Discord Bot. compose.yaml runs this image with the bot's .env, data and logs mounted
# from the checkout; see "Run with Docker" in the README.
FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="llm-discord-bot" \
      org.opencontainers.image.source="https://github.com/Adriwin06/LLM-Discord-Bot"

# ffmpeg/ffprobe handle audio and video attachments; Tesseract does OCR for text-only models.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# The bot runs as the uid/gid that owns data/, logs/ and .backup/ on the host, so it can keep
# writing them through the bind mounts. 1000 is the usual first Linux account.
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd --gid "${APP_GID}" bot \
 && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /usr/sbin/nologin bot \
 && mkdir -p /home/bot/.cache /app \
 && chown bot:bot /home/bot/.cache /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so a code-only change reuses this layer. requirements.lock pins the
# versions the bot last ran with; a package it does not list installs at its latest version.
COPY requirements.txt requirements.lock ./
RUN pip install -r requirements.txt -c requirements.lock

COPY --chown=bot:bot . .

USER bot
CMD ["python", "main.py"]
