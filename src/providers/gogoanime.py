"""
gogoanime.py — HiAnime streams via Railway-hosted hianime-api proxy.

Architecture:
  iPad → Render (FastAPI) → Railway (hianime-api) → HiAnime → streams

3-step lookup to build the correct animeEpisodeId:
  1. GET /api/v2/hianime/search?q={title}              → animeId  (e.g. "naruto-60")
  2. GET /api/v2/hianime/anime/{animeId}/episodes      → episodes list → episodeId
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
    """GET JSON from hianime-api; raises on HTTP error."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(url, headers={"Accept": "application/json"})
        r.raise_for_status()
        return r.json()


async def _search_anime(title: str) -> str | None:
    """Step 1: search for anime → return animeId string (e.g. 'naruto-60')."""
    try:
        data = await _get(f"{ANIWATCH_BASE}/api/v2/hianime/search?q={title}")
        animes = (
            data.get("data", {}).get("animes")
            or data.get("animes")
            or []
        )
        if not animes:
            return None
        return animes[0].get("id")
    except Exception as exc:
        logger.debug("hianime search failed for %r: %s", title, exc)
        return None


async def _get_episode_id(anime_id: str, ep: int) -> str | None:
    """Step 2: list episodes for animeId → return episodeId for episode number ep."""
    try:
        data = await _get(f"{ANIWATCH_BASE}/api/v2/hianime/anime/{anime_id}/episodes")
        episodes = (
            data.get("data", {}).get("episodes")
            or data.get("episodes")
            or []
        )
        for ep_obj in episodes:
            if ep_obj.get("number") == ep:
                return ep_obj.get("episodeId")
        return None
    except Exception as exc:
        logger.debug("hianime episodes failed for %r ep %d: %s", anime_id, ep, exc)
        return None


async def _get_sources(episode_id: str, category: str) -> list:
    """Step 3: fetch sources for episodeId → return list of stream dicts."""
    url = (
        f"{ANIWATCH_BASE}/api/v2/hianime/episode/sources"
        f"?animeEpisodeId={episode_id}&server=hd-1&category={category}"
    )
    try:
        data = await _get(url)
        sources = (
            data.get("data", {}).get("sources")
            or data.get("sources")
            or []
        )
        tracks = (
            data.get("data", {}).get("tracks")
            or data.get("tracks")
            or []
        )
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
    """
    Main provider entry point.
    Tries up to 3 title variants from ctx to find the anime on HiAnime,
    then fetches streams for the requested episode.
    """
    ctx = ctx or await build_ctx(anilist_id)
    titles: list[str] = ctx.get("titles", [])
    category = "dub" if audio == "dub" else "sub"

    for title in titles[:3]:
        anime_id = await _search_anime(title)
        if not anime_id:
            logger.debug("gogoanime: no search result for title=%r", title)
            continue

        episode_id = await _get_episode_id(anime_id, ep)
        if not episode_id:
            logger.debug(
                "gogoanime: ep %d not found in anime_id=%r (title=%r)",
                ep, anime_id, title,
            )
            continue

        streams = await _get_sources(episode_id, category)
        if streams:
            logger.info(
                "gogoanime: found %d stream(s) for anilist=%d ep=%d audio=%s via anime_id=%r",
                len(streams), anilist_id, ep, audio, anime_id,
            )
            return streams

        logger.debug(
            "gogoanime: sources empty for episode_id=%r (title=%r ep=%d audio=%s)",
            episode_id, title, ep, audio,
        )

    logger.warning(
        "gogoanime: no streams found for anilist_id=%d ep=%d audio=%s", anilist_id, ep, audio
    )
    return []
