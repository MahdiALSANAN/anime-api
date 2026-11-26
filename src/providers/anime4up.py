"""
Anime4Up provider — Arabic subtitled anime
Base URL: https://w1.anime4up.rest
"""

from __future__ import annotations
import asyncio
import logging
import re
from urllib.parse import urljoin, quote

from src.providers._http import fetch_text
from src.providers._match import best_match
from src.providers._media import build_ctx

log = logging.getLogger(__name__)

BASE = "https://w1.anime4up.rest"
_TIMEOUT = 20.0


# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────

async def _get(url: str, **kw) -> str:
    return await fetch_text(url, timeout=_TIMEOUT, **kw)


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


# ──────────────────────────────────────────────
# slug search
# ──────────────────────────────────────────────

async def _find_slug(titles: list[str]) -> str | None:
    """Search Anime4Up and return the anime page slug."""
    for title in titles:
        query = quote(title)
        try:
            html = await _get(f"{BASE}/?s={query}")
        except Exception as e:
            log.debug("anime4up: search failed for '%s': %s", title, e)
            continue

        soup = _soup(html)
        # search result cards: <div class="anime-card-title"><a href="...">
        links = soup.select("div.anime-card-title a[href], .post-title a[href], h3.entry-title a[href]")
        if not links:
            # fallback: any link containing /anime/
            links = [a for a in soup.find_all("a", href=True) if "/anime/" in a["href"]]

        candidates = []
        for a in links:
            href = a.get("href", "")
            text = a.get_text(strip=True)
            if href and text:
                candidates.append((text, href))

        if not candidates:
            log.debug("anime4up: no candidates for '%s'", title)
            continue

        texts = [c[0] for c in candidates]
        idx = best_match(title, texts)
        if idx is not None:
            matched_href = candidates[idx][1]
            # extract slug from URL like /anime/انمي-name/
            slug = matched_href.rstrip("/").split("/")[-1]
            log.info("anime4up: matched '%s' → slug=%s", title, slug)
            return matched_href  # return full URL

    log.info("anime4up: no slug found for titles=%s", titles)
    return None


# ──────────────────────────────────────────────
# episode list
# ──────────────────────────────────────────────

async def _parse_episode_list(anime_url: str) -> list[dict]:
    """Return list of {ep: int, url: str} from anime page."""
    try:
        html = await _get(anime_url)
    except Exception as e:
        log.warning("anime4up: failed to load anime page %s: %s", anime_url, e)
        return []

    soup = _soup(html)
    episodes = []

    # selector: episode links in list
    # common patterns on anime4up: ul.episodes-list li a, .episodes-container a
    selectors = [
        "ul.episodes-list li a[href]",
        ".episodes-container a[href]",
        "ul.lst-episodes li a[href]",
        ".ep-list a[href]",
        "div.episodes a[href]",
    ]

    links = []
    for sel in selectors:
        links = soup.select(sel)
        if links:
            break

    if not links:
        # broad fallback: any /episode/ link
        links = [a for a in soup.find_all("a", href=True)
                 if "/episode/" in a.get("href", "")]

    for a in links:
        href = a.get("href", "")
        if not href or "/episode/" not in href:
            continue

        # extract episode number from URL or text
        ep_num = None

        # try URL pattern: الحلقة-{n} or episode-{n}
        m = re.search(r"(?:الحلقة|episode)[_-](\d+)", href, re.IGNORECASE)
        if m:
            ep_num = int(m.group(1))

        if ep_num is None:
            # try link text
            text = a.get_text(strip=True)
            m = re.search(r"(\d+)", text)
            if m:
                ep_num = int(m.group(1))

        if ep_num is not None:
            full_url = href if href.startswith("http") else urljoin(BASE, href)
            episodes.append({"ep": ep_num, "url": full_url})

    # deduplicate
    seen = {}
    for e in episodes:
        seen[e["ep"]] = e["url"]
    return [{"ep": k, "url": v} for k, v in sorted(seen.items())]


# ──────────────────────────────────────────────
# stream extraction
# ──────────────────────────────────────────────

async def _fetch_hls_from_embed(embed_url: str) -> str | None:
    """Resolve embed URL to .m3u8 HLS link."""
    try:
        html = await _get(embed_url)
    except Exception as e:
        log.debug("anime4up: embed fetch failed %s: %s", embed_url, e)
        return None

    # look for m3u8 directly
    m = re.search(r'["\']([^"\']+\.m3u8[^"\']*)["\']', html)
    if m:
        return m.group(1)

    # look for file/source patterns
    m = re.search(r'(?:file|source|src)\s*:\s*["\']([^"\']+)["\']', html)
    if m and ("m3u8" in m.group(1) or "mp4" in m.group(1)):
        return m.group(1)

    # AES-encrypted source (anime4up uses jwplayer with encrypted playlist)
    if "playlist" in html and "encrypted" in html.lower():
        m = re.search(r'playlist\s*:\s*["\']([^"\']+)["\']', html)
        if m:
            return m.group(1)

    return None


async def _extract_streams(ep_url: str) -> list[dict]:
    """
    Load episode watch page and extract video stream URLs.
    Returns list of {url, quality, type} dicts.
    """
    try:
        html = await _get(ep_url)
    except Exception as e:
        log.warning("anime4up: failed to load episode page %s: %s", ep_url, e)
        return []

    soup = _soup(html)
    streams: list[dict] = []

    # ── Method 1: server list items with data-src or data-video ──
    server_selectors = [
        "ul.server-list li",
        "ul.servers-list li",
        ".watch-servers li",
        ".servers li",
        "ul.list-server-items li",
        ".server-item",
    ]

    server_items = []
    for sel in server_selectors:
        server_items = soup.select(sel)
        if server_items:
            log.debug("anime4up: found %d server items via '%s'", len(server_items), sel)
            break

    for item in server_items:
        embed_url = (
            item.get("data-src")
            or item.get("data-video")
            or item.get("data-url")
        )
        if not embed_url:
            a = item.find("a")
            if a:
                embed_url = a.get("href") or a.get("data-src")

        if not embed_url:
            continue

        quality = item.get("data-quality") or item.get_text(strip=True) or "auto"
        embed_url = embed_url if embed_url.startswith("http") else urljoin(BASE, embed_url)

        hls = await _fetch_hls_from_embed(embed_url)
        if hls:
            streams.append({
                "url": hls,
                "quality": quality,
                "type": "hls" if "m3u8" in hls else "mp4",
            })

    if streams:
        return streams

    # ── Method 2: regex scan entire HTML for embed iframes / data-video ──
    log.debug("anime4up: method 1 found nothing, trying regex on page HTML")

    # data-video="..." or data-src="..."
    for attr_val in re.findall(r'data-(?:video|src|url)=["\']([^"\']+)["\']', html):
        if attr_val.startswith("http") and ("embed" in attr_val or "play" in attr_val or "watch" in attr_val):
            hls = await _fetch_hls_from_embed(attr_val)
            if hls:
                streams.append({"url": hls, "quality": "auto", "type": "hls" if "m3u8" in hls else "mp4"})

    if streams:
        return streams

    # ── Method 3: look for m3u8 directly in page ──
    m3u8_urls = re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', html)
    for url in m3u8_urls:
        streams.append({"url": url, "quality": "auto", "type": "hls"})

    # ── Method 4: iframe src as embed ──
    if not streams:
        for iframe in soup.find_all("iframe", src=True):
            src = iframe["src"]
            if any(x in src for x in ["embed", "player", "watch", "play"]):
                hls = await _fetch_hls_from_embed(src)
                if hls:
                    streams.append({"url": hls, "quality": "auto", "type": "hls" if "m3u8" in hls else "mp4"})

    return streams


# ──────────────────────────────────────────────
# public API
# ──────────────────────────────────────────────

async def get_episodes(anilist_id: int, ctx: dict) -> dict:
    """
    Returns {meta, episodes: {sub: [{ep, url}]}}
    """
    titles: list[str] = ctx.get("titles", [])
    if not titles:
        log.warning("anime4up: no titles in ctx for anilist_id=%s", anilist_id)
        return {"meta": {"lang": "ar", "provider": "anime4up"}, "episodes": {"sub": []}}

    anime_url = await _find_slug(titles)
    if not anime_url:
        return {"meta": {"lang": "ar", "provider": "anime4up"}, "episodes": {"sub": []}}

    ep_list = await _parse_episode_list(anime_url)
    log.info("anime4up: found %d episodes for anilist_id=%s", len(ep_list), anilist_id)

    return {
        "meta": {"lang": "ar", "provider": "anime4up", "source_url": anime_url},
        "episodes": {"sub": ep_list},
    }


async def watch(anilist_id: int, audio: str, ep: int, ctx: dict) -> list[dict]:
    """
    Returns list of stream dicts: [{url, quality, type}, ...]
    """
    if audio != "sub":
        log.debug("anime4up: audio='%s' not supported (only 'sub')", audio)
        return []

    titles: list[str] = ctx.get("titles", [])
    if not titles:
        log.warning("anime4up: no titles in ctx for anilist_id=%s", anilist_id)
        return []

    # Find anime page
    anime_url = await _find_slug(titles)
    if not anime_url:
        log.info("anime4up: could not find anime page for anilist_id=%s titles=%s", anilist_id, titles)
        return []

    # Find episode URL
    ep_list = await _parse_episode_list(anime_url)
    ep_url = next((e["url"] for e in ep_list if e["ep"] == ep), None)

    if not ep_url:
        log.info("anime4up: episode %s not found (total episodes found: %d)", ep, len(ep_list))
        # Debug: log what episodes were found
        found_eps = sorted([e["ep"] for e in ep_list])
        log.info("anime4up: available episodes: %s", found_eps[:20])
        return []

    log.info("anime4up: loading episode %s from %s", ep, ep_url)
    streams = await _extract_streams(ep_url)

    if not streams:
        log.info("anime4up: no streams found for episode %s", ep)
    else:
        log.info("anime4up: ✅ found %d stream(s) for episode %s (best: %s)",
                 len(streams), ep, streams[0].get("quality", "?"))

    return streams
