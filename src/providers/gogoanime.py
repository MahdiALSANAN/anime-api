"""
gogoanime.py — HiAnime streams via Railway-hosted hianime-api proxy.

Architecture:
  iPad → Render (FastAPI) → Railway (hianime-api) → HiAnime → streams

3-step lookup to build the correct animeEpisodeId:
  1. GET /api/v2/hianime/search?q={title}        → animeId (e.g. "naruto-60")
  2. GET /api/v2/hianime/anime/{animeId}/episodes → episodeId
  3. GET /api/v2/hianime/episode/sources?animeEpisodeId={episodeId}&server=hd-1&category={sub|dub}
"""

import os
import logging
import httpx
from src.providers._media import build_ctx

NAME = "gogoanime"
logger = logging.getLogger(__name__)

ANIWATCH_BASE = os.environ.get(
    "HIANIME_API",
    "https://hianime-api-production-6dfe2dde.up.railway.app",
).rstrip("/")

TIMEOUT = 20.0


async def _get(url: str) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(url, headers={"Accept": "application/json"})
        r.raise_for_status()
        return r.json()


async def _search_anime(title: str) -> str | None:
    try:
        data = await _get(f"{ANIWATCH_BASE}/api/v2/hianime/search?q={title}")
        animes = data.get("data", {}).get("animes") or data.get("animes") or []
        return animes[0].get("id") if animes else None
    except Exception as exc:
        logger.debug("hianime search failed for %r: %s", title, exc)
        return None


async def _get_episode_id(anime_id: str, ep: int) -> str | None:
    try:
        data = await _get(f"{ANIWATCH_BASE}/api/v2/hianime/anime/{anime_id}/episodes")
        episodes = data.get("data", {}).get("episodes") or data.get("episodes") or []
        for ep_obj in episodes:
            if ep_obj.get("number") == ep:
                return ep_obj.get("episodeId")
        return None
    except Exception as exc:
        logger.debug("hianime episodes failed for %r ep %d: %s", anime_id, ep, exc)
        return None


async def _get_sources(episode_id: str, category: str) -> list:
    url = (
        f"{ANIWATCH_BASE}/api/v2/hianime/episode/sources"
        f"?animeEpisodeId={episode_id}&server=hd-1&category={category}"
    )
    try:
        data = await _get(url)
        sources = data.get("data", {}).get("sources") or data.get("sources") or []
        tracks = data.get("data", {}).get("tracks") or data.get("tracks") or []
        intro = data.get("data", {}).get("intro") or data.get("intro") or {}
        outro = data.get("data", {}).get("outro") or data.get("outro") or {}
        streams = []
        for i, src in enumerate(sources):
            stream_url = src.get("url", "")
            if not stream_url:
                continue
            streams.append({
                "url": stream_url,
                "type": "hls" if ".m3u8" in stream_url else "embed",
                "embed": stream_url,
                "audio": category,
                "server": "HiAnime-hd-1",
                "priority": len(sources) - i,
                "referer": "https://hianime.to/",
                "isActive": i == 0,
                "tracks": tracks,
                "intro": intro,
                "outro": outro,
            })
        return streams
    except Exception as exc:
        logger.debug("hianime sources failed for %r: %s", episode_id, exc)
        return []


async def watch(anilist_id: int, audio: str, ep: int, ctx: dict | None = None) -> list:
    ctx = ctx or await build_ctx(anilist_id)
    titles: list[str] = ctx.get("titles", [])
    category = "dub" if audio == "dub" else "sub"

    for title in titles[:3]:
        anime_id = await _search_anime(title)
        if not anime_id:
            continue

        episode_id = await _get_episode_id(anime_id, ep)
        if not episode_id:
            continue

        streams = await _get_sources(episode_id, category)
        if streams:
            logger.info(
                "gogoanime: %d stream(s) for anilist=%d ep=%d audio=%s via %r",
                len(streams), anilist_id, ep, audio, anime_id,
            )
            return streams

    logger.warning("gogoanime: no streams for anilist=%d ep=%d audio=%s", anilist_id, ep, audio)
    return []
