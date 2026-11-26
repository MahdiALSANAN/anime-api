from __future__ import annotations
import re
import logging
from bs4 import BeautifulSoup
from ._http import fetch_text
from ._match import find_top_slugs

logger = logging.getLogger(__name__)

NAME = "anime3rb"
BASE = "https://anime3rb.com"
LANG = "ar"

_QUALITY_ORDER = {"1080": 0, "720": 1, "480": 2, "360": 3, "240": 4}

async def _search_site(query: str) -> list[dict]:
    url = f"{BASE}/search?q={query}"
    try:
        html = await fetch_text(url, headers={"Referer": BASE + "/"})
    except Exception as e:
        logger.debug("anime3rb: search failed %s: %s", url, e)
        try:
            url2 = f"{BASE}/?s={query}"
            html = await fetch_text(url2, headers={"Referer": BASE + "/"})
        except Exception as e2:
            logger.debug("anime3rb: alt search also failed: %s", e2)
            return []

    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []

    for a in soup.find_all("a", href=re.compile(r"/titles/[^/\"']+$")):
        href = a.get("href", "")
        slug = href.rstrip("/").split("/")[-1]
        title = a.get("title") or a.get_text(strip=True)
        if slug and title:
            results.append({"slug": slug, "text": title})

    if not results:
        for card in soup.select("div.anime-card, article.anime, div.card, div.item"):
            a = card.find("a", href=re.compile(r"/titles/"))
            if not a:
                continue
            href = a.get("href", "")
            slug = href.rstrip("/").split("/")[-1]
            title = (
                card.find("h3") or card.find("h2") or card.find("h1") or
                card.find(class_=re.compile(r"title|name", re.I))
            )
            title_text = title.get_text(strip=True) if title else a.get_text(strip=True)
            if slug and title_text:
                results.append({"slug": slug, "text": title_text})

    seen: set[str] = set()
    unique: list[dict] = []
    for r in results:
        if r["slug"] not in seen:
            seen.add(r["slug"])
            unique.append(r)

    logger.warning("anime3rb: search '%s' → %d results", query, len(unique))
    return unique

async def _search_fn(query: str) -> list[dict]:
    return await _search_site(query)

async def _scrape_anime_page(slug: str) -> dict:
    url = f"{BASE}/titles/{slug}"
    try:
        html = await fetch_text(url, headers={"Referer": BASE + "/"})
    except Exception as e:
        logger.warning("anime3rb: anime page FAILED %s: %s", url, e)
        return {}

    logger.warning("anime3rb: page HTML snippet slug=%s: %s", slug, html[:3000])

    soup = BeautifulSoup(html, "html.parser")
    episodes: list[dict] = []

    for a in soup.find_all("a", href=re.compile(r"/(titles/[^/]+/episodes?/|episode[s]?/)(\d+)")):
        href = a.get("href", "")
        m = re.search(r"/(\d+)\s*$", href.rstrip("/"))
        if not m:
            continue
        ep_no = int(m.group(1))
        ep_url = href if href.startswith("http") else BASE + href
        episodes.append({"ep": ep_no, "url": ep_url})

    if not episodes:
        pattern = re.compile(rf"/{re.escape(slug)}/[^/]*?(\d+)")
        for a in soup.find_all("a", href=pattern):
            href = a.get("href", "")
            m = pattern.search(href)
            if not m:
                continue
            ep_no = int(m.group(1))
            ep_url = href if href.startswith("http") else BASE + href
            episodes.append({"ep": ep_no, "url": ep_url})

    if not episodes:
        for a in soup.find_all("a", href=re.compile(r"episode", re.I)):
            href = a.get("href", "")
            m = re.search(r"(\d+)", href.split("/")[-1] or href.split("/")[-2] or "")
            if not m:
                continue
            ep_no = int(m.group(1))
            ep_url = href if href.startswith("http") else BASE + href
            episodes.append({"ep": ep_no, "url": ep_url})

    seen: set[int] = set()
    unique: list[dict] = []
    for e in episodes:
        if e["ep"] not in seen:
            seen.add(e["ep"])
            unique.append(e)
    unique.sort(key=lambda e: e["ep"])

    logger.warning("anime3rb: slug=%s found %d episodes", slug, len(unique))
    return {"episodes": {"sub": unique}} if unique else {}

async def _extract_streams(ep_url: str) -> list[dict]:
    try:
        html = await fetch_text(ep_url, headers={"Referer": BASE + "/"})
    except Exception as e:
        logger.warning("anime3rb: ep page FAILED %s: %s", ep_url, e)
        return []

    # ← التعديل: نطبع أول 3000 حرف من صفحة الحلقة
    logger.warning("anime3rb: ep_page HTML snippet url=%s: %s", ep_url, html[:3000])

    streams: list[dict] = []
    seen_urls: set[str] = set()

    def add_stream(url: str, quality: str = "unknown"):
        if url not in seen_urls:
            seen_urls.add(url)
            q_key = re.search(r"(\d{3,4})", quality)
            q = q_key.group(1) if q_key else "unknown"
            streams.append({"url": url, "quality": q, "label": f"{q}p" if q != "unknown" else "auto"})

    for m3u8_url in re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', html):
        q_match = re.search(r'(\d{3,4})p?', m3u8_url)
        q = q_match.group(1) if q_match else "unknown"
        add_stream(m3u8_url, q)

    for m in re.finditer(r'["\']?file["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']', html):
        add_stream(m.group(1))

    for m in re.finditer(r'["\']?src["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']', html):
        add_stream(m.group(1))

    for block in re.finditer(r'sources\s*:\s*\[([^\]]+)\]', html):
        for m in re.finditer(r'["\']?(https?://[^\s"\']+\.m3u8[^\s"\']*)["\']?', block.group(1)):
            add_stream(m.group(1))

    for m in re.finditer(r'wire:initial-data=["\']({.+?})["\']', html):
        try:
            import json
            data_str = m.group(1).replace("&quot;", '"')
            data = json.loads(data_str)
            data_text = json.dumps(data)
            for url_m in re.finditer(r'https?://[^\s"\'\\]+\.m3u8[^\s"\'\\]*', data_text):
                add_stream(url_m.group(0))
        except Exception:
            pass

    if not streams:
        soup = BeautifulSoup(html, "html.parser")
        for iframe in soup.find_all("iframe", src=True):
            src = iframe["src"]
            if not src.startswith("http"):
                src = BASE + src
            try:
                embed_html = await fetch_text(src, headers={"Referer": ep_url}, timeout=15.0)
                logger.warning("anime3rb: embed HTML snippet url=%s: %s", src, embed_html[:2000])
                for m3u8_url in re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', embed_html):
                    add_stream(m3u8_url)
                for m in re.finditer(r'["\']?file["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']', embed_html):
                    add_stream(m.group(1))
            except Exception as e:
                logger.debug("anime3rb: embed fetch failed %s: %s", src, e)

    streams.sort(key=lambda s: _QUALITY_ORDER.get(s["quality"], 99))
    logger.warning("anime3rb: ep_url=%s → %d streams", ep_url, len(streams))
    return streams

async def get_episodes(anilist_id: int, ctx: dict) -> dict:
    titles: list[str] = ctx.get("titles", [])
    if not titles:
        return {}

    top_slugs = await find_top_slugs(titles, _search_fn, n=6)
    if not top_slugs:
        logger.warning("anime3rb: no slugs found for %s", titles)
        return {}

    for entry in top_slugs:
        result = await _scrape_anime_page(entry["slug"])
        if result.get("episodes", {}).get("sub"):
            return result

    return {}

async def watch(anilist_id: int, audio: str, ep: int, ctx: dict) -> list[dict]:
    if audio not in ("sub", "ar"):
        return []

    fresh = await get_episodes(anilist_id, ctx)
    fresh_sub: list = fresh.get("episodes", {}).get("sub", [])

    ep_url: str | None = next(
        (e["url"] for e in fresh_sub if e["ep"] == ep), None
    )
    if not ep_url:
        logger.warning("anime3rb: ep %s not found for id=%s, fresh_sub=%s", ep, anilist_id, fresh_sub[:5])
        return []

    streams = await _extract_streams(ep_url)
    logger.warning("anime3rb: id=%s ep=%s → %d streams", anilist_id, ep, len(streams))
    return streams
