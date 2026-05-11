import asyncio
import logging
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import aiohttp


@dataclass
class GiphyGif:
    id: str
    title: str
    url: str
    media_url: str = ""
    rating: str = ""
    analytics: dict[str, Any] | None = None


class GiphyClient:
    """Minimal async client for the GIPHY REST API."""

    BASE_URL = "https://api.giphy.com/v1"

    def __init__(self, config):
        self.api_key = str(getattr(config, "GIPHY_API_KEY", "") or "").strip()
        self.rating = self._normalize_rating(getattr(config, "GIPHY_RATING", "pg-13"))
        self.lang = str(getattr(config, "GIPHY_LANG", "en") or "en").strip().lower()
        self.timeout_seconds = max(1.0, float(getattr(config, "GIPHY_TIMEOUT_SECONDS", 8.0)))
        self._session: aiohttp.ClientSession | None = None
        self._random_ids: dict[str, str] = {}
        self._random_id_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def search_gif(self, query: str, *, user_key: str = "") -> GiphyGif | None:
        gifs = await self.search_gifs(query, limit=1, user_key=user_key)
        return gifs[0] if gifs else None

    async def search_gifs(self, query: str, *, limit: int = 1, user_key: str = "") -> list[GiphyGif]:
        if not self.enabled:
            logging.info("GIPHY search skipped because GIPHY_API_KEY is not configured.")
            return []

        clean_query = self._clean_query(query)
        if not clean_query:
            logging.info("GIPHY search skipped because the query was empty after sanitization.")
            return []

        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 1
        limit = max(1, min(limit, 25))

        params = {
            "api_key": self.api_key,
            "q": clean_query,
            "limit": limit,
            "rating": self.rating,
            "lang": self.lang,
        }
        random_id = await self.random_id_for_user(user_key)
        if random_id:
            params["random_id"] = random_id

        payload = await self._get_json("/gifs/search", params)
        if not payload:
            return []

        data = payload.get("data")
        if not isinstance(data, list) or not data:
            logging.info("GIPHY search returned no results. query=%r", clean_query)
            return []

        gifs = []
        for item in data:
            gif = self._gif_from_payload(item)
            if gif:
                gifs.append(gif)

        if not gifs:
            logging.warning("GIPHY search returned no results with usable GIPHY URLs. query=%r", clean_query)
        return gifs

    async def register_sent(self, gif: GiphyGif, *, user_key: str = ""):
        analytics = gif.analytics or {}
        onsent_url = ((analytics.get("onsent") or {}).get("url") or "").strip()
        if not onsent_url:
            return

        random_id = await self.random_id_for_user(user_key)
        if not random_id:
            return

        ping_url = self._analytics_url(onsent_url, random_id)
        if not ping_url:
            return

        try:
            session = await self._client_session()
            async with session.get(ping_url) as response:
                if response.status >= 400:
                    logging.debug("GIPHY analytics ping failed. status=%s gif_id=%s", response.status, gif.id)
        except Exception as e:
            logging.debug("GIPHY analytics ping failed for gif_id=%s: %s", gif.id, e)

    async def random_id_for_user(self, user_key: str) -> str:
        if not self.enabled:
            return ""

        user_key = str(user_key or "default")
        cached = self._random_ids.get(user_key)
        if cached:
            return cached

        async with self._random_id_lock:
            cached = self._random_ids.get(user_key)
            if cached:
                return cached

            payload = await self._get_json("/randomid", {"api_key": self.api_key}, log_errors=False)
            random_id = ""
            if payload:
                data = payload.get("data")
                if isinstance(data, dict):
                    random_id = str(data.get("random_id") or "").strip()

            if not random_id:
                random_id = secrets.token_hex(16)

            self._random_ids[user_key] = random_id
            return random_id

    async def _get_json(self, path: str, params: dict, *, log_errors: bool = True) -> dict | None:
        url = f"{self.BASE_URL}{path}"
        try:
            session = await self._client_session()
            async with session.get(url, params=params) as response:
                if response.status >= 400:
                    body = await response.text()
                    if log_errors:
                        logging.warning("GIPHY request failed. status=%s path=%s body=%s", response.status, path, body[:300])
                    return None

                payload = await response.json(content_type=None)
        except Exception as e:
            if log_errors:
                logging.warning("GIPHY request failed. path=%s error=%s", path, e)
            return None

        meta = payload.get("meta") if isinstance(payload, dict) else None
        status = meta.get("status") if isinstance(meta, dict) else None
        try:
            status_code = int(status) if status is not None else 0
        except (TypeError, ValueError):
            status_code = 0
        if status_code >= 400:
            if log_errors:
                logging.warning("GIPHY response error. path=%s meta=%s", path, meta)
            return None

        return payload if isinstance(payload, dict) else None

    async def _client_session(self) -> aiohttp.ClientSession:
        if self._session and not self._session.closed:
            return self._session

        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            headers={"Accept-Encoding": "gzip, deflate"},
        )
        return self._session

    def _gif_from_payload(self, item: Any) -> GiphyGif | None:
        if not isinstance(item, dict):
            return None

        url = self._first_giphy_url(item)
        media_url = self._first_giphy_media_url(item)
        if not url:
            return None

        return GiphyGif(
            id=str(item.get("id") or ""),
            title=str(item.get("title") or ""),
            url=url,
            media_url=media_url,
            rating=str(item.get("rating") or ""),
            analytics=item.get("analytics") if isinstance(item.get("analytics"), dict) else None,
        )

    def _first_giphy_url(self, item: dict) -> str:
        for key in ("url", "bitly_gif_url"):
            url = str(item.get(key) or "").strip()
            if self._is_giphy_url(url):
                return url

        images = item.get("images")
        if isinstance(images, dict):
            for rendition in ("original", "downsized", "fixed_height", "fixed_width"):
                rendition_data = images.get(rendition)
                if not isinstance(rendition_data, dict):
                    continue
                url = str(rendition_data.get("url") or "").strip()
                if self._is_giphy_url(url):
                    return url

        return ""

    def _first_giphy_media_url(self, item: dict) -> str:
        images = item.get("images")
        if not isinstance(images, dict):
            return ""

        for rendition in ("original", "downsized", "fixed_height", "fixed_width"):
            rendition_data = images.get(rendition)
            if not isinstance(rendition_data, dict):
                continue
            url = str(rendition_data.get("url") or "").strip()
            if self._is_giphy_url(url):
                return url

        return ""

    def _is_giphy_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
        except ValueError:
            return False

        if parsed.scheme not in {"http", "https"}:
            return False

        host = parsed.netloc.lower().split(":", 1)[0]
        return host == "giphy.com" or host.endswith(".giphy.com")

    def _analytics_url(self, base_url: str, random_id: str) -> str:
        try:
            parsed = urlparse(base_url)
        except ValueError:
            return ""

        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""

        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        params["ts"] = str(int(time.time() * 1000))
        params["random_id"] = random_id
        return urlunparse(parsed._replace(query=urlencode(params)))

    def _clean_query(self, query: str) -> str:
        query = str(query or "")
        query = re.sub(r"https?://\S+", " ", query)
        query = re.sub(r"<@!?\d+>|<@&\d+>|<#\d+>", " ", query)
        query = re.sub(r"@(?:everyone|here)\b", " ", query, flags=re.IGNORECASE)
        query = re.sub(r"\s+", " ", query).strip()
        return query[:50].strip()

    def _normalize_rating(self, rating: str) -> str:
        rating = str(rating or "pg-13").strip().lower()
        if rating in {"g", "pg", "pg-13", "r"}:
            return rating
        logging.warning("Invalid GIPHY_RATING=%r; falling back to pg-13.", rating)
        return "pg-13"
