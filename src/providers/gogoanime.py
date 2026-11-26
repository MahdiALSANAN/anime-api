from __future__ import annotations
import re, logging
from bs4 import BeautifulSoup
from ._http import fetch_text
from ._match import find_top_slugs

logger = logging.getLogger(__name__)
NAME = "gogoanime"
BASE = "https://anitaku.pe"
LANG = "sub"

_Q_ORDER = {"1080": 0, "720": 1, "480": 2, "360": 3}

async def _search_fn(query: str) -> list[dict]:
    url = f"{BASE}/search.html?keyword={query}"
    try:
        html = await fetch_text(url, headers={"Referer": BASE + "/"})
    except Exception as e:
        logger.warning("gogo: search failed: %s", e)
        return []
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for a in soup.select("ul.items li p.name a"):
        href = a.get("href", "")
        slug = href.strip("/").split("/")[-1]
        title = a.get_text(strip=True)
        if slug and title:
            results.append({"slug": slug, "text": title})
    logger.warning("gogo: search '%s' → %d results", query, len(results))
    return results

async def _get_episode_url(slug: str, ep: int) -> str | None:
    # Gogoanime episode URL pattern: /slug-episode-N
    return f"{BASE}/{slug}-episode-{ep}"

async def _extract_streams(ep_url: str) -> list[dict]:
    try:
        html = await fetch_text(ep_url, headers={"Referer": BASE + "/"})
    except Exception as e:
        logger.warning("gogo: ep page failed %s: %s", ep_url, e)
        return []

    soup = BeautifulSoup(html, "html.parser")
    streams: list[dict] = []
    seen: set[str] = set()

    # Find the main embed iframe
    for iframe in soup.select("div.play-video iframe, iframe#playerframe"):
        src = iframe.get("src", "")
        if not src:
            continue
        if not src.startswith("http"):
            src = "https:" + src if src.startswith("//") else BASE + src
        try:
            embed_html = await fetch_text(src, headers={"Referer": ep_url}, timeout=15.0)
        except Exception as e:
            logger.debug("gogo: embed fetch failed %s: %s", src, e)
            continue

        for m3u8 in re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', embed_html):
            if m3u8 not in seen:
                seen.add(m3u8)
                q_m = re.search(r'(\d{3,4})p?', m3u8)
                q = q_m.group(1) if q_m else "unknown"
                streams.append({"url": m3u8, "quality": q, "label": f"{q}p" if q != "unknown" else "auto"})

        for m in re.finditer(r'["\']?file["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']', embed_html):
            u = m.group(1)
            if u not in seen:
                seen.add(u)
                streams.append({"url": u, "quality": "unknown", "label": "auto"})

    streams.sort(key=lambda s: _Q_ORDER.get(s["quality"], 99))
    logger.warning("gogo: ep_url=%s → %d streams", ep_url, len(streams))
    return streams

async def get_episodes(anilist_id: int, ctx: dict) -> dict:
    titles: list[str] = ctx.get("titles", [])
    if not titles:
        return {}
    top = await find_top_slugs(titles, _search_fn, n=5)
    if not top:
        logger.warning("gogo: no slugs for %s", titles)
        return {}
    best = top[0]["slug"]
    # Build episode list from expected_episodes
    n = ctx.get("expected_episodes") or 1
    episodes = [{"ep": i, "url": f"{BASE}/{best}-episode-{i}"} for i in range(1, n + 1)]
    return {"slug": best, "episodes": {"sub": episodes}}

async def watch(anilist_id: int, audio: str, ep: int, ctx: dict) -> list[dict]:
    titles: list[str] = ctx.get("titles", [])
    if not titles:
        return []
    top = await find_top_slugs(titles, _search_fn, n=5)
    if not top:
        return []
    slug = top[0]["slug"]
    ep_url = f"{BASE}/{slug}-episode-{ep}"
    return await _extract_streams(ep_url)
