# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/store.py
import json
import aiofiles
import asyncio
import os
from datetime import datetime
import logging

class Store:
    def __init__(self, settings_path='data/settings.json', data_path='data/data.json', backup_dir='.backup'):
        self.settings_path = settings_path
        self.data_path = data_path
        self.backup_dir = backup_dir
        self.settings_lock = asyncio.Lock()
        self.data_lock = asyncio.Lock()
        
        # Ensure data directory and files exist
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        os.makedirs(os.path.dirname(data_path), exist_ok=True)
        os.makedirs(backup_dir, exist_ok=True)

        if not os.path.exists(self.settings_path):
            with open(self.settings_path, 'w') as f:
                json.dump({}, f)
        if not os.path.exists(self.data_path):
            with open(self.data_path, 'w') as f:
                json.dump({}, f)

    async def _read_json(self, path):
        try:
            async with aiofiles.open(path, 'r', encoding='utf-8') as f:
                return json.loads(await f.read())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    async def _write_json(self, path, data):
        async with aiofiles.open(path, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(data, indent=2))

    async def get_settings(self):
        async with self.settings_lock:
            return await self._read_json(self.settings_path)

    async def save_settings(self, settings):
        async with self.settings_lock:
            await self._write_json(self.settings_path, settings)

    async def get_data(self):
        async with self.data_lock:
            return await self._read_json(self.data_path)

    async def save_data(self, data):
        async with self.data_lock:
            await self._write_json(self.data_path, data)

    async def get_guild_settings(self, guild_id):
        settings = await self.get_settings()
        return settings.get(str(guild_id), {})

    async def get_guild_data(self, guild_id):
        data = await self.get_data()
        return data.get(str(guild_id), {})

    async def save_guild_data(self, guild_id, guild_data):
        async with self.data_lock:
            data = await self._read_json(self.data_path)
            data[str(guild_id)] = guild_data
            await self._write_json(self.data_path, data)

    async def backup_data(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        async with self.settings_lock:
            settings_data = await self._read_json(self.settings_path)
            backup_path = os.path.join(self.backup_dir, f"settings_{timestamp}.json")
            await self._write_json(backup_path, settings_data)
            logging.info(f"Settings backed up to {backup_path}")

        async with self.data_lock:
            data_data = await self._read_json(self.data_path)
            backup_path = os.path.join(self.backup_dir, f"data_{timestamp}.json")
            await self._write_json(backup_path, data_data)
            logging.info(f"Data backed up to {backup_path}")
