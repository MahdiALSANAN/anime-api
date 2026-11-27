"""
gogoanime.py — AllAnime GraphQL API (api.allanime.day)
لا سكرابينج، لا IP blocking، يعطي m3u8 مباشرة.

ctx structure (from build_ctx / _media.py):
  ctx["media"]["title"]["english"]  → "Naruto"
  ctx["media"]["title"]["romaji"]   → "Naruto"
  ctx["media"]["title"]["native"]   → "ナルト"
  ctx["media"]["idMal"]             → 20 (MAL id)
"""
import logging
import httpx

NAME = "gogoanime"
logger = logging.getLogger(__name__)

ALLANIME_API = "https://api.allanime.day/api"
ALLANIME_REFERER = "https://allanime.to"
TIMEOUT = 30.0
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": ALLANIME_REFERER,
    "Origin": "https://allanime.to",
}

SEARCH_QUERY = """
query($search: SearchInput, $translationType: VaildTranslationTypeEnumType, $countryOrigin: VaildCountryOriginEnumType) {
  shows(
    search: $search
    translationType: $translationType
    countryOrigin: $countryOrigin
    page: 1
    perPage: 10
  ) {
    edges {
      _id
      name
      englishName
      nativeName
      malId
      availableEpisodes
    }
  }
}"""

EPISODES_QUERY = """
query($showId: String!, $translationType: VaildTranslationTypeEnumType!, $episodeString: String!) {
  episode(showId: $showId, translationType: $translationType, episodeString: $episodeString) {
    episodeString
    sourceUrls
    notes
  }
}"""


async def _graphql(query: str, variables: dict) -> dict:
    """Execute a GraphQL query against AllAnime API."""
    params = {
        "variables": __import__("json").dumps(variables),
        "query": query,
    }
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS, follow_redirects=True) as client:
        r = await client.get(ALLANIME_API, params=params)
        r.raise_for_status()
        return r.json()


def _decode_url(encoded: str) -> str:
    """AllAnime URL cipher: each char shifted by -56."""
    try:
        result = ""
        for c in encoded:
            if c == "-":
                result += "/"
            elif c.isalpha() and c.islower():
                result += chr(ord(c) - 10)
            else:
                result += c
        return result
    except Exception:
        return encoded


def _decode_url_v2(encoded: str) -> str:
    """Alternative cipher seen in some AllAnime responses."""
    try:
        return "".join(chr(ord(c) - 56) if ord(c) - 56 > 0 else c for c in encoded)
    except Exception:
        return encoded


def _extract_titles(ctx: dict) -> list[str]:
    """Extract candidate title strings from ctx (using media key from _media.py)."""
    titles = []
    media = ctx.get("media") or {}
    title = media.get("title") or {}

    # Priority: english → romaji → native
    for key in ("english", "romaji", "native"):
        val = title.get(key)
        if val and val not in titles:
            titles.append(val)

    # Synonyms
    for syn in media.get("synonyms") or []:
        if syn and syn not in titles:
            titles.append(syn)

    return titles


async def _search(title: str, translation: str) -> str | None:
    """Search AllAnime for the show; returns show _id or None."""
    try:
        data = await _graphql(SEARCH_QUERY, {
            "search": {"query": title, "allowAdult": False, "allowUnknown": False},
            "translationType": translation,
            "countryOrigin": "JP",
        })
        edges = (data.get("data") or {}).get("shows", {}).get("edges") or []
        if not edges:
            logger.debug("AllAnime: no search results for title=%r translation=%s", title, translation)
            return None
        return edges[0]["_id"]
    except Exception as e:
        logger.debug("AllAnime: search error for %r: %s", title, e)
        return None


async def _search_by_mal(mal_id: int, translation: str) -> str | None:
    """Search AllAnime by MAL ID directly."""
    try:
        data = await _graphql(SEARCH_QUERY, {
            "search": {"query": "", "allowAdult": False, "allowUnknown": False},
            "translationType": translation,
        })
        # This doesn't filter by malId directly — use name search instead
        return None
    except Exception:
        return None


async def _get_sources(show_id: str, ep: int, translation: str) -> list:
    """Fetch episode sources from AllAnime; returns list of stream dicts."""
    try:
        data = await _graphql(EPISODES_QUERY, {
            "showId": show_id,
            "translationType": translation,
            "episodeString": str(ep),
        })
        ep_data = (data.get("data") or {}).get("episode")
        if not ep_data:
            logger.debug("AllAnime: no episode data for show=%r ep=%d", show_id, ep)
            return []

        source_urls = ep_data.get("sourceUrls") or []
        streams = []

        for src in source_urls:
            source_url = src.get("sourceUrl", "")
            source_name = src.get("sourceName", "")
            priority = src.get("priority", 0)

            if not source_url:
                continue

            # Skip embed sources that don't have direct URLs
            # AllAnime encodes the URL — try both decoders
            decoded = source_url
            if source_url.startswith("--"):
                # Encoded URL — strip leading "--" then decode
                raw = source_url[2:]
                decoded_v1 = _decode_url(raw)
                decoded_v2 = _decode_url_v2(raw)
                # Use the one that looks like a valid URL
                for candidate in (decoded_v1, decoded_v2):
                    if "http" in candidate or "mp4" in candidate or "m3u8" in candidate:
                        decoded = candidate
                        break
                    decoded = decoded_v2  # fallback

            # Normalize URL
            if not decoded.startswith("http"):
                if decoded.startswith("//"):
                    decoded = "https:" + decoded
                else:
                    continue

            stream_type = "hls" if ".m3u8" in decoded else "mp4" if ".mp4" in decoded else "embed"

            streams.append({
                "url": decoded,
                "type": stream_type,
                "embed": decoded,
                "audio": translation,
                "server": f"AllAnime-{source_name}",
                "priority": priority,
                "referer": ALLANIME_REFERER,
                "isActive": True,
                "tracks": [],
                "intro": {},
                "outro": {},
            })

        # Sort by priority descending
        streams.sort(key=lambda s: s["priority"], reverse=True)
        return streams

    except Exception as e:
        logger.debug("AllAnime: sources error for show=%r ep=%d: %s", show_id, ep, e)
        return []


async def watch(anilist_id: int, audio: str, ep: int, ctx: dict | None = None) -> list:
    """
    Main provider entry point.
    Tries title variants from ctx["media"]["title"] to find the anime on AllAnime.
    """
    if ctx is None:
        from src.providers._media import build_ctx
        ctx = await build_ctx(anilist_id)

    translation = "dub" if audio == "dub" else "sub"
    titles = _extract_titles(ctx)

    if not titles:
        logger.warning("AllAnime: no titles found for anilist_id=%d", anilist_id)
        return []

    logger.debug("AllAnime: trying titles %r for anilist=%d ep=%d audio=%s", titles[:3], anilist_id, ep, audio)

    for title in titles[:4]:
        show_id = await _search(title, translation)
        if not show_id:
            continue

        logger.debug("AllAnime: found show_id=%r for title=%r", show_id, title)
        streams = await _get_sources(show_id, ep, translation)

        if streams:
            logger.info(
                "AllAnime: %d stream(s) for anilist=%d ep=%d audio=%s via title=%r show_id=%r",
                len(streams), anilist_id, ep, audio, title, show_id,
            )
            return streams

        logger.debug("AllAnime: no streams from show_id=%r (title=%r)", show_id, title)

    logger.warning("AllAnime: no streams found for anilist_id=%d ep=%d audio=%s", anilist_id, ep, audio)
    return []
