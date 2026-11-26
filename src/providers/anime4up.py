from __future__ import annotations
import base64
import re
import logging
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from ._http import fetch_text
from ._match import find_top_slugs

logger = logging.getLogger(__name__)

NAME = "anime4up"
BASE = "https://w1.anime4up.rest"
LANG = "ar"

_AES_KEY = b"4^TT%zCMd4L4ma4Z"
_AES_IV  = b"4^TT%zCMd4L4ma4Z"

_QUALITY_ORDER = {"1080": 0, "720": 1, "480": 2, "360": 3, "240": 4}

def _decrypt_server_link(encoded: str) -> str:
    raw = base64.b64decode(encoded)
    cipher = AES.new(_AES_KEY, AES.MODE_CBC, _AES_IV)
    return unpad(cipher.decrypt(raw), AES.block_size).decode()

def _resolve_video_url(encoded: str) -> str | None:
    try:
        return _decrypt_server_link(encoded)
    except Exception:
        pass
    try:
        return base64.b64decode(encoded).decode()
    except Exception:
        pass
    if encoded.startswith("http"):
        return encoded
    return None

async def _fetch_hls_from_embed(embed_url: str) -> list[str]:
    try:
        html = await fetch_text(embed_url)
    except Exception as e:
        logger.debug("embed fetch failed %s: %s", embed_url, e)
        return []
    urls: list[str] = []
    urls += re.findall(r'https?://[^\s"\']+\.m3u8[^\s"\']*', html)
    for m in re.finditer(r'sources\s*:\s*\[([^\]]+)\]', html):
        urls += re.findall(r'["\']?(https?://[^\s"\']+\.m3u8[^\s"\']*)["\']?', m.group(1))
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

async def _extract_streams(ep_url: str) -> list[dict]:
    try:
        html = await fetch_text(ep_url)
    except Exception as e:
        logger.warning("anime4up: ep page FAILED %s: %s", ep_url, e)
        return []
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select("ul.server-list li")
    logger.warning("anime4up: ep_url=%s server_items=%d html_len=%d", ep_url, len(items), len(html))
    streams: list[dict] = []
    for li in items:
        encoded = li.get("data-video") or li.get("data-src") or ""
        if not encoded:
            continue
        embed_url = _resolve_video_url(encoded.strip())
        if not embed_url:
            continue
        quality_label = li.get_text(strip=True)
        quality_key = re.search(r'(\d{3,4})', quality_label)
        quality = quality_key.group(1) if quality_key else "unknown"
        hls_urls = await _fetch_hls_from_embed(embed_url)
        for hls in hls_urls:
            streams.append({"url": hls, "quality": quality, "label": f"{quality}p"})
    streams.sort(key=lambda s: _QUALITY_ORDER.get(s["quality"], 99))
    return streams

async def _search_site(query: str) -> list[dict]:
    url = f"{BASE}/?s={query}"
    try:
        html = await fetch_text(url)
    except Exception as e:
        logger.debug("search failed %s: %s", url, e)
        return []
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []
    for card in soup.select("div.anime-card-details"):
        a = card.select_one("a")
        if not a:
            continue
        href  = a.get("href", "")
        title = a.get_text(strip=True) or a.get("title", "")
        slug  = href.rstrip("/").split("/")[-1]
        if slug:
            results.append({"slug": slug, "title": title, "url": href})
    return results

async def _search_fn(query: str) -> list[dict]:
    items = await _search_site(query)
    return [{"slug": r["slug"], "text": r["title"]} for r in items]

async def _scrape_anime_page(slug: str) -> dict:
    url = f"{BASE}/anime/{slug}/"
    try:
        html = await fetch_text(url)
    except Exception as e:
        logger.warning("anime4up: anime page FAILED %s: %s", url, e)
        return {}
    soup = BeautifulSoup(html, "html.parser")
    episodes: list[dict] = []
    for a in soup.find_all("a", href=re.compile(r'/episode/')):
        href = a.get("href", "")
        ep_no = re.search(r'الحلقة[- _]*(\d+)', href)
        if not ep_no:
            ep_no = re.search(r'episode[- _]*(\d+)', href, re.IGNORECASE)
        if href and ep_no:
            episodes.append({"ep": int(ep_no.group(1)), "url": href})
    seen: set[int] = set()
    unique: list[dict] = []
    for e in episodes:
        if e["ep"] not in seen:
            seen.add(e["ep"])
            unique.append(e)
    logger.warning("anime4up: slug=%s found %d episodes", slug, len(unique))
    unique.sort(key=lambda e: e["ep"])
    return {"episodes": {"sub": unique}}

async def get_episodes(anilist_id: int, ctx: dict) -> dict:
    titles: list[str] = ctx.get("titles", [])
    if not titles:
        logger.debug("anime4up: no titles in ctx for id=%s", anilist_id)
        return {}
    top_slugs = await find_top_slugs(titles, _search_fn, n=6)
    if not top_slugs:
        logger.debug("anime4up: no slugs found for %s", titles)
        return {}
    for entry in top_slugs:
        result = await _scrape_anime_page(entry["slug"])
        if result.get("episodes", {}).get("sub"):
            logger.debug("anime4up: matched slug=%s score=%.2f", entry["slug"], entry.get("score", 0))
            return result
    return {}

async def watch(anilist_id: int, audio: str, ep: int, ctx: dict) -> list[dict]:
    if audio != "sub":
        return []
    fresh = await get_episodes(anilist_id, ctx)
    fresh_sub: list = fresh.get("episodes", {}).get("sub", [])
    ep_url: str | None = next(
        (e["url"] for e in fresh_sub if e["ep"] == ep), None
    )
    if not ep_url:
        logger.debug("anime4up: ep %s not found for id=%s", ep, anilist_id)
        return []
    streams = await _extract_streams(ep_url)
    logger.debug("anime4up: id=%s ep=%s → %d streams", anilist_id, ep, len(streams))
    return streams
