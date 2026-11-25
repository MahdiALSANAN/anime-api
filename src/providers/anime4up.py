# src/providers/anime4up.py — Anime4Up Arabic provider
# يعتمد على بنية الموقع: /anime/{slug}/ و /episode/{episode-slug}/

import re
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup

from src.providers import _cache
from src.providers._http import fetch_text

NAME = "anime4up"
BASE = "https://www.anime4up.bond"
LANG = "ar"

SEARCH_TTL = 600
SHOW_TTL = 1800


async def _get(url: str, referer: str | None = None) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        "Referer": referer or BASE + "/",
    }
    try:
        return await fetch_text(url, headers) or ""
    except Exception as e:
        print(f"[ANIME4UP] fetch error {url}: {e}", flush=True)
        return ""


def _absolute_url(url: str) -> str:
    return urljoin(BASE + "/", (url or "").strip())


def _slug_from_url(url: str, section: str) -> str | None:
    path = urlparse(_absolute_url(url)).path
    m = re.search(rf"/{re.escape(section)}/([^/?#]+)", path, re.IGNORECASE)
    return m.group(1).strip("/") if m else None


# ---------------------------------------------------------
# Search
# ---------------------------------------------------------

async def search(query: str) -> list:
    key = f"a4u-search:{quote(query)}"
    hit = _cache.cached(key, SEARCH_TTL)
    if hit is not None:
        return hit

    results = []
    seen = set()

    # Anime4Up يعتمد على ووردبريس: نجرب البحث القياسي أولاً
    for url in [
        f"{BASE}/?s={quote(query)}",
        f"{BASE}/?search_param=animes&s={quote(query)}",
    ]:
        body = await _get(url)
        if not body:
            continue
        soup = BeautifulSoup(body, "html.parser")

        # البطاقات قد تكون div أو article أو li حسب القالب
        for card in soup.select(
            "div.anime-card-container, div.anime-card-poster, "
            "article.anime, div.anime-list-content .anime-card, "
            "a[href*='/anime/']"
        ):
            a = card if card.name == "a" else card.find("a", href=True)
            if not a or not a.get("href"):
                continue
            href = a["href"]
            slug = _slug_from_url(href, "anime")
            if not slug or slug in seen:
                continue
            seen.add(slug)

            title_el = None
            if card.name != "a":
                title_el = card.select_one(
                    "h3, h2, .anime-card-title, .title, .anime-card-title span"
                )
            title = title_el.get_text(" ", strip=True) if title_el else ""
            if not title:
                title = a.get("title") or slug.replace("-", " ")

            results.append({"slug": slug, "text": title})

        if results:
            break

    _cache.set(key, results, SEARCH_TTL)
    return results


# ---------------------------------------------------------
# Episodes
# ---------------------------------------------------------

async def _fetch_episodes(slug: str) -> list:
    key = f"a4u-show:{slug}"
    hit = _cache.cached(key, SHOW_TTL)
    if hit is not None:
        return hit

    url = f"{BASE}/anime/{slug}/"
    body = await _get(url)
    if not body:
        return []

    soup = BeautifulSoup(body, "html.parser")
    episodes = []
    seen = set()

    # البحث عن روابط الحلقات (/episode/...)
    for a in soup.select("a[href*='/episode/']"):
        href = a.get("href", "")
        m = re.search(r"/episode/([^/?#]+)", href)
        if not m:
            continue
        ep_slug = m.group(1)
        if ep_slug in seen:
            continue
        seen.add(ep_slug)

        # استخراج رقم الحلقة من النص أو الرابط
        text = a.get_text(" ", strip=True)
        num = None
        m_num = re.search(r"(\d+)", text)
        if m_num:
            num = int(m_num.group(1))
        else:
            m_slug = re.search(r"(\d+)(?:-|$)", ep_slug)
            if m_slug:
                num = int(m_slug.group(1))

        if num is None:
            continue

        episodes.append({
            "number": num,
            "title": text or f"الحلقة {num}",
            "epSlug": _absolute_url(href),
        })

    episodes.sort(key=lambda x: x["number"])
    _cache.set(key, episodes, SHOW_TTL)
    return episodes


# ---------------------------------------------------------
# Streams
# ---------------------------------------------------------

async def _extract_streams(ep_url: str) -> list:
    body = await _get(ep_url)
    if not body:
        return []

    soup = BeautifulSoup(body, "html.parser")
    streams = []
    seen = set()

    # 1. إذا كان هناك iframe مباشر
    for iframe in soup.select("iframe[src]"):
        src = _absolute_url(iframe.get("src"))
        if src in seen:
            continue
        seen.add(src)
        streams.append({
            "url": src,
            "type": "embed",
            "quality": iframe.get("title") or "auto",
            "server": "anime4up-iframe",
            "referer": ep_url,
        })

    # 2. روابط سيرفرات (Watch / Server)
    for el in soup.select(
        "ul#watch-servers li a[href], ul.servers li a[href], "
        "div.server-list a[href], a[data-server]"
    ):
        href = el.get("href") or el.get("data-server") or ""
        if not href.startswith("http"):
            href = _absolute_url(href)
        if href in seen:
            continue
        seen.add(href)
        streams.append({
            "url": href,
            "type": "embed",
            "quality": el.get_text(" ", strip=True) or "auto",
            "server": "anime4up-server",
            "referer": ep_url,
        })

    return streams


# ---------------------------------------------------------
# Provider interface
# ---------------------------------------------------------

async def get_episodes(anilist_id: int, ctx: dict) -> dict:
    titles = _titles_from_ctx(ctx)
    if not titles:
        return {"episodes": {}}

    candidates = await _find_candidates(titles)
    for cand in candidates:
        eps = await _fetch_episodes(cand["slug"])
        if eps:
            return {
                "meta": {"id": cand["slug"], "title": cand["text"], "source": NAME, "lang": LANG},
                "episodes": {"sub": eps, "dub": []},
            }
    return {"episodes": {}}


async def watch(anilist_id: int, audio: str, ep: int, ctx: dict) -> list:
    if audio != "sub":
        return []
    titles = _titles_from_ctx(ctx)
    if not titles:
        return []

    candidates = await _find_candidates(titles)
    for cand in candidates:
        eps = await _fetch_episodes(cand["slug"])
        target = next((e for e in eps if e["number"] == ep), None)
        if target:
            streams = await _extract_streams(target["epSlug"])
            if streams:
                return streams
    return []


def _titles_from_ctx(ctx: dict) -> list:
    media = (ctx or {}).get("media") or {}
    t = media.get("title") or {}
    out = [t.get("english"), t.get("romaji"), t.get("native"), *(media.get("synonyms") or [])]
    return [x for x in out if x]


async def _find_candidates(titles: list) -> list:
    """بحث مبسط: يأخذ أول نتيجة تطابق جزئيًا من العنوان."""
    for title in titles:
        results = await search(title)
        if results:
            return results[:3]
    return []
