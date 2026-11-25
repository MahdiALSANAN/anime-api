"""
witanime.py — Arabic anime provider.
Provides Arabic-subbed streams for ACEanime app.
"""

import re
import json
import html as html_lib
import difflib

from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlparse

from src.providers import _cache
from src.providers._http import fetch_text
from src.providers._match import (
    build_titles,
    decode_entities,
    episode_meta,
    expected_count,
    find_top_slugs,
    get_prequel_offset,
    select_series,
    strip_tags,
)
from src.providers._media import build_ctx


NAME = "witanime"
BASE = "https://witanime.site"
LANG = "ar"

_SITEMAP_CACHE = None


# ---------------------------------------------------------
# HTML parser
# ---------------------------------------------------------

class _AnchorParser(HTMLParser):

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return

        attrs = dict(attrs)

        self.current = {
            "href": attrs.get("href", ""),
            "title": attrs.get("title", ""),
            "text": "",
        }

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"] += data

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self.current is not None:
            self.current["text"] = self.current["text"].strip()
            self.anchors.append(self.current)
            self.current = None


def _parse_anchors(source: str) -> list:
    parser = _AnchorParser()

    try:
        parser.feed(source or "")
    except Exception as e:
        print(
            f"[WITANIME] HTML parsing warning: {e}",
            flush=True,
        )

    return parser.anchors


def _absolute_url(url: str) -> str:
    url = decode_entities((url or "").strip())
    return urljoin(BASE + "/", url)


def _slug_from_url(url: str, section: str):
    parsed = urlparse(_absolute_url(url))

    match = re.search(
        rf"/{re.escape(section)}/([^/?#]+)",
        parsed.path,
        re.IGNORECASE,
    )

    return match.group(1).strip("/") if match else None


def _watch_info(url: str):
    parsed = urlparse(_absolute_url(url))

    match = re.search(
        r"/watch/([^/]+)/(\d+)",
        parsed.path,
        re.IGNORECASE,
    )

    if not match:
        return None

    return {
        "slug": match.group(1),
        "number": int(match.group(2)),
        "url": _absolute_url(url),
    }


def _normalize_title(value: str) -> str:
    value = html_lib.unescape(
        decode_entities(value or "")
    )

    value = strip_tags(value).lower()
    value = re.sub(r"[-_]+", " ", value)
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()

    return value


def _headers(referer: str = None) -> dict:
    return {
        "Referer": referer or BASE + "/",
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
    }


async def _safe_fetch(url: str, referer: str = None) -> str:
    try:
        result = await fetch_text(url, _headers(referer))
        return result or ""
    except Exception as e:
        print(
            f"[WITANIME] Request failed: {url} | "
            f"{type(e).__name__}: {e}",
            flush=True,
        )
        return ""


# ---------------------------------------------------------
# Search result extraction
# ---------------------------------------------------------

def _extract_anime_results(source: str) -> list:
    results = []
    seen = set()

    for anchor in _parse_anchors(source):
        href = anchor.get("href", "")
        slug = _slug_from_url(href, "anime")

        if not slug or slug.lower() in seen:
            continue

        title = (
            anchor.get("text")
            or anchor.get("title")
            or slug.replace("-", " ")
        )

        title = strip_tags(title).strip()

        if not title:
            title = slug.replace("-", " ")

        seen.add(slug.lower())

        results.append({
            "slug": slug,
            "text": title,
        })

    patterns = [
        r"""href\s*=\s*["']([^"']*/anime/[^"'?#]+)""",
        r"""["'](https?://witanime\.site/anime/[^"'?#]+)""",
        r"""["'](//witanime\.site/anime/[^"'?#]+)""",
        r"""["'](/anime/[^"'?#]+)""",
    ]

    for pattern in patterns:
        for match in re.finditer(
            pattern,
            source or "",
            re.IGNORECASE,
        ):
            href = html_lib.unescape(match.group(1))
            slug = _slug_from_url(href, "anime")

            if not slug or slug.lower() in seen:
                continue

            seen.add(slug.lower())

            results.append({
                "slug": slug,
                "text": slug.replace("-", " "),
            })

    return results


# ---------------------------------------------------------
# Search
# ---------------------------------------------------------

async def search(query: str) -> list:
    print(f"[WITANIME] Searching: {query}", flush=True)

    results = []
    seen = set()

    def add_items(items):
        for item in items:
            slug = (item.get("slug") or "").strip()
            if not slug or slug.lower() in seen:
                continue
            seen.add(slug.lower())
            results.append(item)

    # استخدام مسار البحث القياسي في ووردبريس
    search_url = f"{BASE}/?s={quote(query)}"
    body = await _safe_fetch(search_url)

    print(f"[WITANIME] Search page received: {len(body)} characters", flush=True)

    if body:
        add_items(_extract_anime_results(body))

    if not results:
        api_url = f"{BASE}/wp-json/wp/v2/anime?search={quote(query)}&per_page=30"
        api_body = await _safe_fetch(api_url)
        if api_body:
            try:
                data = json.loads(api_body)
                if isinstance(data, list):
                    for item in data:
                        link = item.get("link", "")
                        slug = _slug_from_url(link, "anime")
                        if slug:
                            title_data = item.get("title", {})
                            title = title_data.get("rendered", "") if isinstance(title_data, dict) else str(title_data)
                            add_items([{"slug": slug, "text": strip_tags(title).strip() or slug.replace("-", " ")}])
            except Exception:
                pass

    print(f"[WITANIME] Search returned {len(results)} results for {query}", flush=True)
    return results


async def search_fn(query: str) -> list:
    return await search(query)


# ---------------------------------------------------------
# Episode list
# ---------------------------------------------------------

async def scrape_series(slug: str) -> list:
    url = f"{BASE}/anime/{slug}"
    body = await _safe_fetch(url)

    if not body:
        return []

    episodes = []
    seen = set()

    for anchor in _parse_anchors(body):
        href = anchor.get("href", "")
        info = _watch_info(href)

        if not info or info["slug"].lower() != slug.lower():
            continue

        number = info["number"]
        if number in seen:
            continue

        label = anchor.get("text") or anchor.get("title") or f"الحلقة {number}"
        label = strip_tags(label).strip()

        episodes.append({
            "number": number,
            "title": label or f"الحلقة {number}",
            "epSlug": info["url"],
            "hasSub": True,
            "hasDub": False,
        })
        seen.add(number)

    watch_pattern = re.compile(
        r"""(?:(?:https?:)?//witanime\.site)?"""
        r"""(/watch/([^/"'\s?#]+)/(\d+))""",
        re.IGNORECASE,
    )

    for match in watch_pattern.finditer(body):
        episode_slug = match.group(2)
        number = int(match.group(3))

        if episode_slug.lower() != slug.lower() or number in seen:
            continue

        episodes.append({
            "number": number,
            "title": f"الحلقة {number}",
            "epSlug": _absolute_url(match.group(1)),
            "hasSub": True,
            "hasDub": False,
        })
        seen.add(number)

    episodes.sort(key=lambda item: item["number"])
    return episodes


# ---------------------------------------------------------
# Stream extraction
# ---------------------------------------------------------

def _add_stream(streams, url, server, priority, referer):
    url = decode_entities((url or "").strip())
    if not url:
        return

    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = urljoin(BASE, url)

    if not url.startswith(("http://", "https://")):
        return

    if any(item["url"] == url for item in streams):
        return

    lower_url = url.lower()
    stream_type = "hls" if ".m3u8" in lower_url else ("mp4" if ".mp4" in lower_url else "embed")
    quality = "1080p" if "1080" in lower_url else ("720p" if "720" in lower_url else ("480p" if "480" in lower_url else "auto"))

    streams.append({
        "url": url,
        "type": stream_type,
        "quality": quality,
        "audio": LANG,
        "server": server,
        "referer": referer,
        "priority": priority,
        "isActive": len(streams) == 0,
    })


def _extract_media_urls(source, streams, server, referer, priority):
    patterns = [
        r"""["']((?:https?:)?//[^"'\s<>]+?\.(?:m3u8|mp4)(?:\?[^"']*)?)["']""",
        r"""(?:file|src)\s*[:=]\s*["']([^"']+\.(?:m3u8|mp4)(?:\?[^"']*)?)["']""",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, source or "", re.IGNORECASE):
            value = match.group(1).replace("\\/", "/")
            if ".m3u8" in value.lower() or ".mp4" in value.lower():
                _add_stream(streams, value, server, priority, referer)


async def scrape_episode_streams(ep_slug: str) -> list:
    url = ep_slug if ep_slug.startswith("http") else _absolute_url(ep_slug)
    body = await _safe_fetch(url)

    if not body:
        return []

    streams = []
    _extract_media_urls(body, streams, "witanime-direct", url, 10)

    iframe_pattern = re.compile(r"""<iframe\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
    embeds = []

    for match in iframe_pattern.finditer(body):
        embed_url = _absolute_url(match.group(1))
        host = urlparse(embed_url).netloc.lower()

        if "videa.hu" in host:
            if embed_url not in embeds:
                embeds.append(embed_url)
                try:
                    videa_body = await fetch_text(embed_url, _headers(url))
                    _extract_media_urls(videa_body, streams, "videa-direct", embed_url, 10)
                except Exception:
                    pass
        else:
            _add_stream(streams, embed_url, f"witanime-embed-{host}", 5, url)

    data_patterns = [r"""data-src\s*=\s*["']([^"']+)["']""", r"""data-video\s*=\s*["']([^"']+)["']"""]
    for pattern in data_patterns:
        for match in re.finditer(pattern, body, re.IGNORECASE):
            value = match.group(1)
            if value.startswith(("http://", "https://", "//", "/")):
                _add_stream(streams, _absolute_url(value), "witanime-data", 6, url)

    return streams


# ---------------------------------------------------------
# Resolve anime
# ---------------------------------------------------------

async def resolve_series(anilist_id: int, ctx: dict | None = None) -> dict:
    ctx = ctx or await build_ctx(anilist_id)
    key = f"np:{NAME}:{anilist_id}"
    hit = _cache.cached(key, _cache.SHOW_IDENTITY_TTL)

    if hit is not None:
        return hit

    media = ctx["media"]
    
    # تجنب الترميز الخربان: استخدام العناوين الإنجليزية والروماجي فقط
    media_titles = media.get("title", {})
    titles = [
        media_titles.get("english"),
        media_titles.get("romaji"),
        media_titles.get("native"),
        *(media.get("synonyms") or [])
    ]
    titles = [t for t in titles if t and t.isascii()]

    candidates = await find_top_slugs(titles, search_fn)
    expected = expected_count(media, ctx.get("anizip"))

    try:
        offset = await get_prequel_offset(anilist_id)
    except Exception:
        offset = 0

    is_single_movie = str((media or {}).get("format") or "").upper() == "MOVIE" or expected == 1

    selected = await select_series(
        candidates, scrape_series, expected, media.get("status"), offset,
        min_score=0.9 if is_single_movie else 0.6,
    )

    if not selected:
        raise RuntimeError(f"witanime: no confident match found for AniList {anilist_id}")

    data = {
        "slug": selected["slug"],
        "title": selected["title"],
        "mode": selected["mode"],
        "offset": offset,
        "score": selected["score"],
    }

    _cache.set(key, data, _cache.SHOW_IDENTITY_TTL)
    return data


def _build_lists(anilist_id, series, provider_eps, ctx, expected) -> dict:
    sub = []
    for src in provider_eps:
        number = src["number"] - series["offset"] if series["mode"] == "offset" else src["number"]
        if number < 1:
            continue
        
        meta = episode_meta(number, ctx)
        base = {
            "number": number,
            "title": src.get("title") or f"الحلقة {number}",
            "duration": meta["duration"],
            "filler": meta["filler"],
            "uncensored": meta["uncensored"],
            "description": meta["description"],
            "image": meta["image"],
            "airDate": meta["airDate"],
            "sourceNumber": src["number"],
        }

        if src.get("hasSub"):
            sub.append({
                "id": f"watch/{NAME}/{anilist_id}/sub/{NAME}-{number}",
                **base,
                "audio": "sub",
                "lang": LANG,
            })

    return {"sub": sub, "dub": []}


# ---------------------------------------------------------
# Public provider interface
# ---------------------------------------------------------

async def get_episodes(anilist_id: int, ctx: dict | None = None) -> dict:
    ctx = ctx or await build_ctx(anilist_id)
    media = ctx["media"]
    series = await resolve_series(anilist_id, ctx)
    episodes = await scrape_series(series["slug"])
    expected = expected_count(media, ctx.get("anizip"))

    return {
        "meta": {
            "id": series["slug"],
            "title": series["title"],
            "source": NAME,
            "lang": LANG,
            "matchScore": round(series["score"], 3),
            "numbering": series["mode"],
            "episodeOffset": series["offset"] if series["mode"] == "offset" else 0,
        },
        "episodes": _build_lists(anilist_id, series, episodes, ctx, expected),
    }


async def watch(anilist_id: int, audio: str, ep: int, ctx: dict | None = None) -> list:
    ctx = ctx or await build_ctx(anilist_id)
    series = await resolve_series(anilist_id, ctx)
    provider_ep = int(ep) + series["offset"] if series["mode"] == "offset" else int(ep)
    episodes = await scrape_series(series["slug"])

    found = next((item for item in episodes if item["number"] == provider_ep), None)
    if not found:
        raise RuntimeError(f"witanime: episode {provider_ep} not found")

    streams = await scrape_episode_streams(found["epSlug"])
    return streams
