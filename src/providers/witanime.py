
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
    """
    WitAnime episode URL:
    /watch/naruto/1
    """

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


def _episode_number(value: str):
    value = decode_entities(value or "")

    patterns = [
        r"episode[-_/ ]*(\d+)",
        r"ep[-_/ ]*(\d+)",
        r"[-_](\d+)(?:[-_/]|$)",
        r"الحلقة\s*(\d+)",
        r"الحلقه\s*(\d+)",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            value,
            re.IGNORECASE,
        )

        if match:
            return int(match.group(1))

    return None


# ---------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------

def _headers(referer: str = None) -> dict:
    return {
        "Referer": referer or BASE + "/",
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }


async def _safe_fetch(url: str, referer: str = None) -> str:
    try:
        result = await fetch_text(
            url,
            _headers(referer),
        )

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

    # Method 1: normal HTML anchors.
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

    # Method 2: URLs embedded in HTML or scripts.
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
# WordPress API search
# ---------------------------------------------------------

async def _search_wordpress_api(query: str) -> list:
    results = []
    endpoints = [
        (
            f"{BASE}/wp-json/wp/v2/anime"
            f"?search={quote(query)}&per_page=30"
        ),
        (
            f"{BASE}/wp-json/wp/v2/animes"
            f"?search={quote(query)}&per_page=30"
        ),
    ]

    for url in endpoints:
        body = await _safe_fetch(url)

        if not body:
            continue

        try:
            data = json.loads(body)
        except Exception:
            continue

        if not isinstance(data, list):
            continue

        for item in data:
            if not isinstance(item, dict):
                continue

            link = item.get("link", "")
            slug = _slug_from_url(link, "anime")

            if not slug:
                continue

            title_data = item.get("title", {})

            if isinstance(title_data, dict):
                title = title_data.get("rendered", "")
            else:
                title = str(title_data or "")

            results.append({
                "slug": slug,
                "text": (
                    strip_tags(title).strip()
                    or slug.replace("-", " ")
                ),
            })

    return results


# ---------------------------------------------------------
# Sitemap search
# ---------------------------------------------------------

async def _load_sitemap() -> list:
    global _SITEMAP_CACHE

    if _SITEMAP_CACHE is not None:
        return _SITEMAP_CACHE

    _SITEMAP_CACHE = []

    sitemap_urls = [
        f"{BASE}/wp-sitemap-posts-anime-1.xml",
        f"{BASE}/wp-sitemap.xml",
        f"{BASE}/sitemap_index.xml",
        f"{BASE}/anime-sitemap.xml",
        f"{BASE}/sitemap.xml",
    ]

    discovered = []

    for sitemap_url in sitemap_urls:
        body = await _safe_fetch(sitemap_url)

        if not body or "<loc>" not in body.lower():
            continue

        locations = re.findall(
            r"<loc>\s*(.*?)\s*</loc>",
            body,
            re.IGNORECASE | re.DOTALL,
        )

        if not locations:
            continue

        # A sitemap index may point to an anime sitemap.
        for location in locations:
            location = html_lib.unescape(location.strip())

            if "sitemap" in location.lower():
                discovered.append(location)

            elif _slug_from_url(location, "anime"):
                discovered.append(location)

    # Include discovered sitemap URLs.
    for sitemap_url in dict.fromkeys(discovered):
        if _slug_from_url(sitemap_url, "anime"):
            locations = [sitemap_url]
        else:
            body = await _safe_fetch(sitemap_url)

            locations = re.findall(
                r"<loc>\s*(.*?)\s*</loc>",
                body or "",
                re.IGNORECASE | re.DOTALL,
            )

        for location in locations:
            location = html_lib.unescape(location.strip())
            slug = _slug_from_url(location, "anime")

            if slug:
                _SITEMAP_CACHE.append({
                    "slug": slug,
                    "text": slug.replace("-", " "),
                })

    unique = {}

    for item in _SITEMAP_CACHE:
        unique[item["slug"].lower()] = item

    _SITEMAP_CACHE = list(unique.values())

    print(
        f"[WITANIME] Sitemap anime entries: "
        f"{len(_SITEMAP_CACHE)}",
        flush=True,
    )

    return _SITEMAP_CACHE


async def _search_sitemap(query: str) -> list:
    entries = await _load_sitemap()

    query_norm = _normalize_title(query)

    if not query_norm:
        return []

    query_words = set(query_norm.split())
    ranked = []

    for item in entries:
        title_norm = _normalize_title(
            item["text"] + " " + item["slug"].replace("-", " ")
        )

        title_words = set(title_norm.split())

        similarity = difflib.SequenceMatcher(
            None,
            query_norm,
            title_norm,
        ).ratio()

        overlap = (
            len(query_words & title_words) / len(query_words)
            if query_words
            else 0
        )

        score = max(similarity, overlap)

        if (
            score >= 0.35
            or query_norm in title_norm
            or title_norm in query_norm
        ):
            ranked.append((score, item))

    ranked.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return [
        item
        for _, item in ranked[:30]
    ]


# ---------------------------------------------------------
# Search
# ---------------------------------------------------------

async def search(query: str) -> list:
    print(
        f"[WITANIME] Searching: {query}",
        flush=True,
    )

    results = []
    seen = set()

    def add_items(items):
        for item in items:
            slug = (item.get("slug") or "").strip()

            if not slug or slug.lower() in seen:
                continue

            seen.add(slug.lower())
            results.append(item)

    # 1. Website search.
    search_url = (
        f"{BASE}/?search_param=animes"
        f"&s={quote(query)}"
    )

    body = await _safe_fetch(search_url)

    print(
        f"[WITANIME] Search page received: "
        f"{len(body)} characters",
        flush=True,
    )

    if body:
        add_items(_extract_anime_results(body))

    # 2. WordPress REST API.
    if not results:
        api_results = await _search_wordpress_api(query)
        add_items(api_results)

        print(
            f"[WITANIME] WordPress API results: "
            f"{len(api_results)}",
            flush=True,
        )

    # 3. Sitemap fallback.
    if not results:
        sitemap_results = await _search_sitemap(query)
        add_items(sitemap_results)

    print(
        f"[WITANIME] Search returned {len(results)} "
        f"results for {query}",
        flush=True,
    )

    for item in results[:10]:
        print(
            f"[WITANIME] Candidate: "
            f"{item['text']} -> {item['slug']}",
            flush=True,
        )

    if not results:
        print(
            f"[WITANIME] No results found for: {query}",
            flush=True,
        )

    return results


async def search_fn(query: str) -> list:
    return await search(query)


# ---------------------------------------------------------
# Episode list
# ---------------------------------------------------------

async def scrape_series(slug: str) -> list:
    url = f"{BASE}/anime/{slug}"

    print(
        f"[WITANIME] Loading anime page: {url}",
        flush=True,
    )

    body = await _safe_fetch(url)

    if not body:
        print(
            f"[WITANIME] Anime page returned empty HTML: {url}",
            flush=True,
        )
        return []

    episodes = []
    seen = set()

    # Method 1: episode anchors.
    for anchor in _parse_anchors(body):
        href = anchor.get("href", "")
        info = _watch_info(href)

        if not info:
            continue

        if info["slug"].lower() != slug.lower():
            continue

        number = info["number"]

        if number in seen:
            continue

        label = (
            anchor.get("text")
            or anchor.get("title")
            or f"الحلقة {number}"
        )

        label = strip_tags(label).strip()

        episodes.append({
            "number": number,
            "title": label or f"الحلقة {number}",
            "epSlug": info["url"],
            "hasSub": True,
            "hasDub": False,
        })

        seen.add(number)

    # Method 2: watch URLs embedded in scripts or HTML.
    watch_pattern = re.compile(
        r"""(?:(?:https?:)?//witanime\.site)?"""
        r"""(/watch/([^/"'\s?#]+)/(\d+))""",
        re.IGNORECASE,
    )

    for match in watch_pattern.finditer(body):
        episode_slug = match.group(2)
        number = int(match.group(3))

        if episode_slug.lower() != slug.lower():
            continue

        if number in seen:
            continue

        episode_url = _absolute_url(match.group(1))

        episodes.append({
            "number": number,
            "title": f"الحلقة {number}",
            "epSlug": episode_url,
            "hasSub": True,
            "hasDub": False,
        })

        seen.add(number)

    episodes.sort(
        key=lambda item: item["number"]
    )

    print(
        f"[WITANIME] Found {len(episodes)} "
        f"episodes for {slug}",
        flush=True,
    )

    if not episodes:
        print(
            "[WITANIME] No /watch/ episode links found.",
            flush=True,
        )

        for anchor in _parse_anchors(body)[:20]:
            print(
                "[WITANIME] LINK:",
                repr(anchor.get("href", "")),
                "| TEXT:",
                repr(anchor.get("text", "")[:80]),
                flush=True,
            )

    return episodes


# ---------------------------------------------------------
# Stream extraction
# ---------------------------------------------------------

def _add_stream(
    streams: list,
    url: str,
    server: str,
    priority: int,
    referer: str,
):
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

    if ".m3u8" in lower_url:
        stream_type = "hls"

    elif ".mp4" in lower_url:
        stream_type = "mp4"

    else:
        stream_type = "embed"

    quality = "auto"

    if "1080" in lower_url:
        quality = "1080p"
    elif "720" in lower_url:
        quality = "720p"
    elif "480" in lower_url:
        quality = "480p"

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


def _extract_media_urls(
    source: str,
    streams: list,
    server: str,
    referer: str,
    priority: int,
):
    patterns = [
        r"""["']((?:https?:)?//[^"'\s<>]+?\.(?:m3u8|mp4)(?:\?[^"']*)?)["']""",
        r"""(?:file|src)\s*[:=]\s*["']([^"']+\.(?:m3u8|mp4)(?:\?[^"']*)?)["']""",
        r"""["']file["']\s*:\s*["']([^"']+)["']""",
        r"""sources\s*:\s*\[\s*\{\s*["']file["']\s*:\s*["']([^"']+)["']""",
    ]

    for pattern in patterns:
        for match in re.finditer(
            pattern,
            source or "",
            re.IGNORECASE,
        ):
            value = match.group(1).replace("\\/", "/")

            if (
                ".m3u8" not in value.lower()
                and ".mp4" not in value.lower()
            ):
                continue

            _add_stream(
                streams,
                value,
                server,
                priority,
                referer,
            )

    escaped_pattern = re.compile(
        r"""(https?:\\?/\\?/[^"'\\\s]+?\.(?:m3u8|mp4)(?:\?[^"'\\\s]*)?)""",
        re.IGNORECASE,
    )

    for match in escaped_pattern.finditer(source or ""):
        value = match.group(1).replace("\\/", "/")

        _add_stream(
            streams,
            value,
            server,
            priority,
            referer,
        )


# ---------------------------------------------------------
# Videa resolver
# ---------------------------------------------------------

async def _resolve_videa(
    embed_url: str,
    streams: list,
    episode_url: str,
):
    print(
        f"[WITANIME] Resolving Videa embed: {embed_url}",
        flush=True,
    )

    try:
        body = await fetch_text(
            embed_url,
            {
                **_headers(episode_url),
                "Origin": "https://videa.hu",
            },
            timeout=25.0,
        )

        before = len(streams)

        _extract_media_urls(
            body or "",
            streams,
            "videa-direct",
            embed_url,
            10,
        )

        print(
            f"[WITANIME] Videa direct streams found: "
            f"{len(streams) - before}",
            flush=True,
        )

    except Exception as e:
        print(
            f"[WITANIME] Videa resolution failed: "
            f"{type(e).__name__}: {e}",
            flush=True,
        )


# ---------------------------------------------------------
# Episode stream extraction
# ---------------------------------------------------------

async def scrape_episode_streams(ep_slug: str) -> list:
    if ep_slug.startswith(("http://", "https://")):
        url = ep_slug
    else:
        url = _absolute_url(ep_slug)

    print(
        f"[WITANIME] Loading episode: {url}",
        flush=True,
    )

    body = await _safe_fetch(url)

    if not body:
        raise RuntimeError(
            f"witanime: episode page returned empty HTML: {url}"
        )

    streams = []

    # 1. Direct media URLs on WitAnime.
    _extract_media_urls(
        body,
        streams,
        "witanime-direct",
        url,
        10,
    )

    # 2. iframe sources.
    iframe_pattern = re.compile(
        r"""<iframe\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""",
        re.IGNORECASE,
    )

    embeds = []

    for match in iframe_pattern.finditer(body):
        embed_url = _absolute_url(match.group(1))
        host = urlparse(embed_url).netloc.lower()

        if "videa.hu" in host:
            if embed_url not in embeds:
                embeds.append(embed_url)

                await _resolve_videa(
                    embed_url,
                    streams,
                    url,
                )
        else:
            _add_stream(
                streams,
                embed_url,
                f"witanime-embed-{host}",
                5,
                url,
            )

    # 3. data-src / data-embed / data-video.
    data_patterns = [
        r"""data-src\s*=\s*["']([^"']+)["']""",
        r"""data-embed\s*=\s*["']([^"']+)["']""",
        r"""data-video\s*=\s*["']([^"']+)["']""",
    ]

    for pattern in data_patterns:
        for match in re.finditer(
            pattern,
            body,
            re.IGNORECASE,
        ):
            value = match.group(1)

            if not value.startswith((
                "http://",
                "https://",
                "//",
                "/",
            )):
                continue

            embed_url = _absolute_url(value)
            host = urlparse(embed_url).netloc.lower()

            if "videa.hu" in host:
                if embed_url not in embeds:
                    embeds.append(embed_url)

                    await _resolve_videa(
                        embed_url,
                        streams,
                        url,
                    )
            else:
                _add_stream(
                    streams,
                    embed_url,
                    "witanime-data",
                    6,
                    url,
                )

    # 4. Open Graph video.
    og_pattern = re.compile(
        r"""<meta\b[^>]*(?:property|name)\s*=\s*["']og:video(?::url)?["'][^>]*content\s*=\s*["']([^"']+)["']""",
        re.IGNORECASE,
    )

    for match in og_pattern.finditer(body):
        value = _absolute_url(match.group(1))

        _add_stream(
            streams,
            value,
            "witanime-og-video",
            7,
            url,
        )

    # 5. Keep embed links as fallback.
    # An iframe URL is not necessarily playable by AVPlayer.
    for embed_url in embeds:
        if not any(
            item["url"] == embed_url
            for item in streams
        ):
            _add_stream(
                streams,
                embed_url,
                "videa-embed",
                1,
                url,
            )

    print(
        f"[WITANIME] Extracted {len(streams)} stream links "
        f"for {url}",
        flush=True,
    )

    for stream in streams:
        print(
            f"[WITANIME] Stream: "
            f"type={stream['type']} "
            f"server={stream['server']} "
            f"url={stream['url'][:180]}",
            flush=True,
        )

    return streams


# ---------------------------------------------------------
# Resolve anime
# ---------------------------------------------------------

async def resolve_series(
    anilist_id: int,
    ctx: dict | None = None,
) -> dict:

    ctx = ctx or await build_ctx(anilist_id)

    key = f"np:{NAME}:{anilist_id}"

    hit = _cache.cached(
        key,
        _cache.SHOW_IDENTITY_TTL,
    )

    if hit is not None:
        print(
            f"[WITANIME] Using cached match for AniList "
            f"{anilist_id}",
            flush=True,
        )
        return hit

    media = ctx["media"]

    titles = build_titles(
        media,
        ctx.get("anizip"),
    )

    print(
        f"[WITANIME] Resolving AniList {anilist_id}",
        flush=True,
    )

    candidates = await find_top_slugs(
        titles,
        search_fn,
    )

    print(
        f"[WITANIME] Found {len(candidates)} "
        f"matching candidates",
        flush=True,
    )

    expected = expected_count(
        media,
        ctx.get("anizip"),
    )

    try:
        offset = await get_prequel_offset(anilist_id)

    except Exception as e:
        print(
            f"[WITANIME] Prequel offset failed: {e}",
            flush=True,
        )
        offset = 0

    is_single_movie = (
        str(
            (media or {}).get("format") or ""
        ).upper() == "MOVIE"
        or expected == 1
    )

    selected = await select_series(
        candidates,
        scrape_series,
        expected,
        media.get("status"),
        offset,
        min_score=0.9 if is_single_movie else 0.6,
    )

    if not selected:
        raise RuntimeError(
            f"witanime: no confident match found "
            f"for AniList {anilist_id}"
        )

    data = {
        "slug": selected["slug"],
        "title": selected["title"],
        "mode": selected["mode"],
        "offset": offset,
        "score": selected["score"],
    }

    print(
        f"[WITANIME] Matched: {data['title']} "
        f"(slug={data['slug']}, score={data['score']})",
        flush=True,
    )

    _cache.set(
        key,
        data,
        _cache.SHOW_IDENTITY_TTL,
    )

    return data


# ---------------------------------------------------------
# Episode response builder
# ---------------------------------------------------------

def _build_lists(
    anilist_id: int,
    series: dict,
    provider_eps: list,
    ctx: dict,
    expected,
) -> dict:

    sub = []

    for src in provider_eps:
        number = (
            src["number"] - series["offset"]
            if series["mode"] == "offset"
            else src["number"]
        )

        if number < 1:
            continue

        if expected and number > expected:
            continue

        meta = episode_meta(number, ctx)

        base = {
            "number": number,
            "title": (
                meta["title"]
                or src.get("title")
                or f"الحلقة {number}"
            ),
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
                "id": (
                    f"watch/{NAME}/{anilist_id}/"
                    f"sub/{NAME}-{number}"
                ),
                **base,
                "audio": "sub",
                "lang": LANG,
            })

    return {
        "sub": sub,
        "dub": [],
    }


# ---------------------------------------------------------
# Public provider interface
# ---------------------------------------------------------

async def get_episodes(
    anilist_id: int,
    ctx: dict | None = None,
) -> dict:

    ctx = ctx or await build_ctx(anilist_id)

    media = ctx["media"]

    series = await resolve_series(
        anilist_id,
        ctx,
    )

    episodes = await scrape_series(
        series["slug"],
    )

    expected = expected_count(
        media,
        ctx.get("anizip"),
    )

    return {
        "meta": {
            "id": series["slug"],
            "title": series["title"],
            "source": NAME,
            "lang": LANG,
            "matchScore": round(series["score"], 3),
            "numbering": series["mode"],
            "episodeOffset": (
                series["offset"]
                if series["mode"] == "offset"
                else 0
            ),
        },
        "episodes": _build_lists(
            anilist_id,
            series,
            episodes,
            ctx,
            expected,
        ),
    }


async def watch(
    anilist_id: int,
    audio: str,
    ep: int,
    ctx: dict | None = None,
) -> list:

    ctx = ctx or await build_ctx(anilist_id)

    print(
        f"[WITANIME] Watch request: "
        f"AniList={anilist_id}, "
        f"episode={ep}, audio={audio}",
        flush=True,
    )

    series = await resolve_series(
        anilist_id,
        ctx,
    )

    provider_ep = (
        int(ep) + series["offset"]
        if series["mode"] == "offset"
        else int(ep)
    )

    episodes = await scrape_series(
        series["slug"],
    )

    found = next(
        (
            item for item in episodes
            if item["number"] == provider_ep
        ),
        None,
    )

    if not found:
        raise RuntimeError(
            f"witanime: episode {provider_ep} "
            f"not found for AniList {anilist_id}; "
            f"available episodes={len(episodes)}"
        )

    streams = await scrape_episode_streams(
        found["epSlug"],
    )

    if not streams:
        raise RuntimeError(
            f"witanime: no playable stream links "
            f"found for episode {provider_ep}"
        )

    return streams
