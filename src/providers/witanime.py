
"""
witanime.py — Arabic anime provider.
Provides Arabic-subbed streams for ACEanime app.
"""

import re
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


# ---------------------------------------------------------
# HTML parser
# ---------------------------------------------------------

class _AnchorParser(HTMLParser):
    """Extract anchor links and their visible text."""

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


def _parse_anchors(html: str) -> list:
    parser = _AnchorParser()

    try:
        parser.feed(html)
    except Exception as e:
        print(
            f"[WITANIME] HTML parsing warning: {e}",
            flush=True
        )

    return parser.anchors


def _absolute_url(url: str) -> str:
    return urljoin(
        BASE + "/",
        decode_entities(url.strip())
    )


def _slug_from_url(url: str, section: str):
    parsed = urlparse(_absolute_url(url))

    match = re.search(
        rf"/{re.escape(section)}/([^/?#]+)",
        parsed.path,
        re.IGNORECASE
    )

    return match.group(1).strip("/") if match else None


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
            re.IGNORECASE
        )

        if match:
            return int(match.group(1))

    return None


# ---------------------------------------------------------
# Search
# ---------------------------------------------------------

async def search(query: str) -> list:
    url = (
        f"{BASE}/?search_param=animes"
        f"&s={quote(query)}"
    )

    print(
        f"[WITANIME] Searching: {query}",
        flush=True
    )

    html = await fetch_text(
        url,
        {
            "Referer": BASE + "/",
            "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        }
    )

    print(
        f"[WITANIME] Search page received: "
        f"{len(html)} characters",
        flush=True
    )

    anchors = _parse_anchors(html)

    results = []
    seen = set()

    for anchor in anchors:
        href = anchor.get("href", "")
        slug = _slug_from_url(href, "anime")

        if not slug or slug in seen:
            continue

        title = (
            anchor.get("text")
            or anchor.get("title")
            or slug.replace("-", " ")
        )

        title = strip_tags(title).strip()

        if not title:
            title = slug.replace("-", " ")

        seen.add(slug)

        results.append({
            "slug": slug,
            "text": title,
        })

    print(
        f"[WITANIME] Search returned "
        f"{len(results)} anime results",
        flush=True
    )

    # Diagnostic output to identify the new site's HTML structure.
    if not results:
        print(
            "[WITANIME] No /anime/ links found. "
            "Sample links from search page:",
            flush=True
        )

        for anchor in anchors[:20]:
            print(
                "[WITANIME] LINK:",
                repr(anchor.get("href", "")),
                "| TEXT:",
                repr(anchor.get("text", "")[:100]),
                flush=True
            )

        title_match = re.search(
            r"<title[^>]*>(.*?)</title>",
            html,
            re.IGNORECASE | re.DOTALL
        )

        if title_match:
            print(
                "[WITANIME] Page title:",
                strip_tags(title_match.group(1)).strip(),
                flush=True
            )

    return results


async def search_fn(query: str) -> list:
    return await search(query)


# ---------------------------------------------------------
# Episode list
# ---------------------------------------------------------

async def scrape_series(slug: str) -> list:
    url = f"{BASE}/anime/{slug}/"

    print(
        f"[WITANIME] Loading anime page: {url}",
        flush=True
    )

    html = await fetch_text(
        url,
        {
            "Referer": BASE + "/",
            "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        }
    )

    anchors = _parse_anchors(html)

    episodes = []
    seen = set()

    for anchor in anchors:
        href = anchor.get("href", "")
        ep_slug = _slug_from_url(href, "episode")

        if not ep_slug:
            continue

        number = _episode_number(ep_slug)

        if number is None:
            number = _episode_number(
                anchor.get("text", "")
            )

        if number is None:
            number = _episode_number(
                anchor.get("title", "")
            )

        if number is None or number in seen:
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
            "epSlug": ep_slug,
            "hasSub": True,
            "hasDub": False,
        })

        seen.add(number)

    episodes.sort(
        key=lambda item: item["number"]
    )

    print(
        f"[WITANIME] Found {len(episodes)} episodes "
        f"for {slug}",
        flush=True
    )

    if not episodes:
        print(
            "[WITANIME] No episode links found. "
            "Sample links from anime page:",
            flush=True
        )

        for anchor in anchors[:20]:
            print(
                "[WITANIME] LINK:",
                repr(anchor.get("href", "")),
                "| TEXT:",
                repr(anchor.get("text", "")[:100]),
                flush=True
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
    url = decode_entities(url.strip())

    if not url:
        return

    if url.startswith("//"):
        url = "https:" + url

    elif url.startswith("/"):
        url = urljoin(BASE, url)

    if not url.startswith(("http://", "https://")):
        return

    if any(
        item["url"] == url
        for item in streams
    ):
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


async def scrape_episode_streams(ep_slug: str) -> list:
    url = f"{BASE}/episode/{ep_slug}/"

    print(
        f"[WITANIME] Loading episode: {url}",
        flush=True
    )

    html = await fetch_text(
        url,
        {
            "Referer": BASE + "/",
            "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        }
    )

    streams = []

    # 1. Direct m3u8 and mp4 URLs
    direct_patterns = [
        r"""["']((?:https?:)?//[^"'\s<>]+?\.(?:m3u8|mp4)(?:\?[^"']*)?)["']""",
        r"""(?:file|src)\s*[:=]\s*["']([^"']+\.(?:m3u8|mp4)(?:\?[^"']*)?)["']""",
    ]

    for pattern in direct_patterns:
        for match in re.finditer(
            pattern,
            html,
            re.IGNORECASE
        ):
            _add_stream(
                streams,
                match.group(1),
                "witanime-direct",
                10,
                url,
            )

    # 2. JWPlayer sources
    jw_patterns = [
        r"""file\s*:\s*["']([^"']+)["']""",
        r"""["']file["']\s*:\s*["']([^"']+)["']""",
        r"""sources\s*:\s*\[\s*\{\s*["']file["']\s*:\s*["']([^"']+)["']""",
    ]

    for pattern in jw_patterns:
        for match in re.finditer(
            pattern,
            html,
            re.IGNORECASE
        ):
            _add_stream(
                streams,
                match.group(1),
                "witanime-jwplayer",
                9,
                url,
            )

    # 3. iframe sources
    iframe_pattern = re.compile(
        r"""<iframe\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""",
        re.IGNORECASE
    )

    for match in iframe_pattern.finditer(html):
        embed_url = _absolute_url(match.group(1))

        _add_stream(
            streams,
            embed_url,
            f"witanime-embed-{urlparse(embed_url).netloc}",
            5,
            url,
        )

    # 4. data-src and data-embed attributes
    data_patterns = [
        r"""data-src\s*=\s*["']([^"']+)["']""",
        r"""data-embed\s*=\s*["']([^"']+)["']""",
        r"""data-video\s*=\s*["']([^"']+)["']""",
    ]

    for pattern in data_patterns:
        for match in re.finditer(
            pattern,
            html,
            re.IGNORECASE
        ):
            value = match.group(1)

            if not value.startswith((
                "http://",
                "https://",
                "//",
                "/"
            )):
                continue

            _add_stream(
                streams,
                value,
                "witanime-data",
                6,
                url,
            )

    # 5. Escaped URLs inside JavaScript
    escaped_pattern = re.compile(
        r"""(https?:\\?/\\?/[^"'\\\s]+?\.(?:m3u8|mp4)(?:\?[^"'\\\s]*)?)""",
        re.IGNORECASE
    )

    for match in escaped_pattern.finditer(html):
        link = match.group(1).replace("\\/", "/")

        _add_stream(
            streams,
            link,
            "witanime-javascript",
            8,
            url,
        )

    print(
        f"[WITANIME] Extracted {len(streams)} streams "
        f"for episode {ep_slug}",
        flush=True
    )

    for stream in streams:
        print(
            f"[WITANIME] Stream: "
            f"type={stream['type']} "
            f"server={stream['server']}",
            flush=True
        )

    return streams


# ---------------------------------------------------------
# Resolve anime
# ---------------------------------------------------------

async def resolve_series(
    anilist_id: int,
    ctx: dict | None = None
) -> dict:
    ctx = ctx or await build_ctx(anilist_id)

    key = f"np:{NAME}:{anilist_id}"

    hit = _cache.cached(
        key,
        _cache.SHOW_IDENTITY_TTL
    )

    if hit is not None:
        print(
            f"[WITANIME] Using cached match "
            f"for AniList {anilist_id}",
            flush=True
        )

        return hit

    media = ctx["media"]

    titles = build_titles(
        media,
        ctx.get("anizip")
    )

    print(
        f"[WITANIME] Resolving AniList {anilist_id}",
        flush=True
    )

    candidates = await find_top_slugs(
        titles,
        search_fn
    )

    print(
        f"[WITANIME] Found {len(candidates)} "
        f"matching candidates",
        flush=True
    )

    expected = expected_count(
        media,
        ctx.get("anizip")
    )

    try:
        offset = await get_prequel_offset(
            anilist_id
        )

    except Exception as e:
        print(
            f"[WITANIME] Prequel offset failed: {e}",
            flush=True
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
        min_score=(
            0.9 if is_single_movie else 0.6
        ),
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
        f"(slug={data['slug']}, "
        f"score={data['score']})",
        flush=True
    )

    _cache.set(
        key,
        data,
        _cache.SHOW_IDENTITY_TTL
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
    expected
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

        meta = episode_meta(
            number,
            ctx
        )

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
    ctx: dict | None = None
) -> dict:
    ctx = ctx or await build_ctx(anilist_id)

    media = ctx["media"]

    series = await resolve_series(
        anilist_id,
        ctx
    )

    episodes = await scrape_series(
        series["slug"]
    )

    expected = expected_count(
        media,
        ctx.get("anizip")
    )

    return {
        "meta": {
            "id": series["slug"],
            "title": series["title"],
            "source": NAME,
            "lang": LANG,
            "matchScore": round(
                series["score"],
                3
            ),
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
            expected
        ),
    }


async def watch(
    anilist_id: int,
    audio: str,
    ep: int,
    ctx: dict | None = None
) -> list:
    ctx = ctx or await build_ctx(anilist_id)

    print(
        f"[WITANIME] Watch request: "
        f"AniList={anilist_id}, "
        f"episode={ep}, audio={audio}",
        flush=True
    )

    series = await resolve_series(
        anilist_id,
        ctx
    )

    provider_ep = (
        int(ep) + series["offset"]
        if series["mode"] == "offset"
        else int(ep)
    )

    episodes = await scrape_series(
        series["slug"]
    )

    found = next(
        (
            item for item in episodes
            if item["number"] == provider_ep
        ),
        None
    )

    if not found:
        raise RuntimeError(
            f"witanime: episode {provider_ep} "
            f"not found for AniList {anilist_id}; "
            f"available episodes={len(episodes)}"
        )

    streams = await scrape_episode_streams(
        found["epSlug"]
    )

    if not streams:
        raise RuntimeError(
            f"witanime: no playable stream links "
            f"found for episode {provider_ep} "
            f"(slug={found['epSlug']})"
        )

    return streams
