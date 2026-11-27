"""
gogoanime.py — AllAnime GraphQL API (api.allanime.day)
لا سكرابينج، لا IP blocking، يعطي m3u8 مباشرة.

Flow:
  1. POST graphql → search by title → get AllAnime ID
  2. POST graphql → get episode sources for that ID + episode number
"""

import logging
import re
import httpx

from src.providers._media import build_ctx

NAME = "gogoanime"
logger = logging.getLogger(__name__)

ALLANIME_API = "https://api.allanime.day/api"
ALLANIME_REFERER = "https://allanime.to"
TIMEOUT = 25.0

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": ALLANIME_REFERER,
}

# GraphQL query — search
SEARCH_QUERY = """
query($search: SearchInput, $translationType: VaildTranslationTypeEnumType) {
  shows(
    search: $search
    translationType: $translationType
    page: 1
    perPage: 5
  ) {
    edges {
      _id
      name
      englishName
      availableEpisodes
    }
  }
}
"""

# GraphQL query — episode sources
SOURCES_QUERY = """
query($showId: String!, $translationType: VaildTranslationTypeEnumType!, $episodeString: String!) {
  episode(
    showId: $showId
    translationType: $translationType
    episodeString: $episodeString
  ) {
    sourceUrls
  }
}
"""


async def _graphql(query: str, variables: dict) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS) as client:
        r = await client.post(
            ALLANIME_API,
            json={"query": query, "variables": variables},
        )
        r.raise_for_status()
        return r.json()


def _decode_url(encoded: str) -> str:
    """AllAnime encodes source URLs with a simple cipher (each char - 56)."""
    if not encoded or encoded.startswith("http"):
        return encoded
    try:
        decoded = "".join(
            chr(ord(c) - 56) if c != "-" else c
            for c in encoded
        )
        return decoded
    except Exception:
        return encoded


async def _search(title: str, translation: str) -> str | None:
    """Step 1: search AllAnime → return show _id."""
    try:
        data = await _graphql(SEARCH_QUERY, {
            "search": {"query": title, "allowAdult": False},
            "translationType": translation,
        })
        edges = data.get("data", {}).get("shows", {}).get("edges", [])
        if edges:
            return edges[0].get("_id")
    except Exception as exc:
        logger.debug("AllAnime search failed for %r: %s", title, exc)
    return None


async def _get_sources(show_id: str, ep: int, translation: str) -> list:
    """Step 2: get episode sources → return list of stream dicts."""
    try:
        data = await _graphql(SOURCES_QUERY, {
            "showId": show_id,
            "translationType": translation,
            "episodeString": str(ep),
        })
        source_urls = (
            data.get("data", {}).get("episode", {}).get("sourceUrls", [])
            or []
        )

        streams = []
        for src in source_urls:
            raw_url = src.get("sourceUrl", "")
            url = _decode_url(raw_url)
            if not url or not url.startswith("http"):
                continue
            stream_type = "hls" if ".m3u8" in url else "mp4"
            streams.append({
                "url": url,
                "type": stream_type,
                "audio": translation,
                "server": src.get("sourceName", "AllAnime"),
                "priority": src.get("priority", 0),
                "referer": ALLANIME_REFERER,
                "isActive": True,
                "tracks": [],
                "intro": {},
                "outro": {},
            })

        # sort by priority descending
        streams.sort(key=lambda x: x["priority"], reverse=True)
        return streams

    except Exception as exc:
        logger.debug("AllAnime sources failed for show=%r ep=%d: %s", show_id, ep, exc)
        return []


async def watch(anilist_id: int, audio: str, ep: int, ctx: dict | None = None) -> list:
    ctx = ctx or await build_ctx(anilist_id)
    titles: list[str] = ctx.get("titles", [])
    translation = "dub" if audio == "dub" else "sub"

    for title in titles[:4]:
        show_id = await _search(title, translation)
        if not show_id:
            logger.debug("AllAnime: no result for title=%r", title)
            continue

        streams = await _get_sources(show_id, ep, translation)
        if streams:
            logger.info(
                "AllAnime: found %d stream(s) for anilist=%d ep=%d audio=%s",
                len(streams), anilist_id, ep, audio,
            )
            return streams

        logger.debug("AllAnime: no sources for show_id=%r ep=%d", show_id, ep)

    logger.warning("AllAnime: no streams found for anilist_id=%d ep=%d", anilist_id, ep)
    return []
