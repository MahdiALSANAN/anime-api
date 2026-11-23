"""
witanime.py — Arabic anime provider (witanime.com)
Provides Arabic-subbed streams for ACEanime app.
"""

import asyncio
import re
from urllib.parse import quote, urlparse

from src.providers import _cache
from src.providers._http import fetch_text
from src.providers._match import (
    attr, build_titles, decode_entities, episode_meta, expected_count,
    find_top_slugs, get_prequel_offset, select_series, strip_tags,
)
from src.providers._media import build_ctx

NAME = "witanime"
BASE = "https://witanime.cyou"
LANG = "ar"

# ------------------------------------------------------------------
# Search
# ------------------------------------------------------------------

async def search(query: str) -> list:
    url = f"{BASE}/?search_param=animes&s={quote(query)}"
    html = await fetch_text(url, {"Referer": BASE})
    results = []
    # Each result: <div class="anime-card-container"> ... <a href="/anime/SLUG/"> ... title ...
    for m in re.finditer(
        r'<div[^>]*class=["\'][^"\']*anime-card-container[^"\']*["\'][^>]*>([\s\S]*?)</div>\s*</div>\s*</div>',
        html, re.IGNORECASE
    ):
        block = m.group(1)
        # Extract href
        link_m = re.search(r'<a\b[^>]*href=["\']([^"\']*)["\'][^>]*class=["\'][^"\']*anime-card-title[^"\']*["\']', block, re.IGNORECASE)
        if not link_m:
            link_m = re.search(r'<a\b[^>]*href=["\'](' + re.escape(BASE) + r'/anime/[^"\']+)["\']', block, re.IGNORECASE)
        if not link_m:
            continue
        href = link_m.group(1).rstrip("/")
        slug_m = re.search(r'/anime/([^/?#]+)', href)
        if not slug_m:
            continue
        slug = slug_m.group(1)
        # Extract title
        title_m = re.search(r'class=["\'][^"\']*anime-card-title[^"\']*["\'][^>]*>([\s\S]*?)</(?:h3|a|div|span)', block, re.IGNORECASE)
        text = strip_tags(title_m.group(1)).strip() if title_m else slug.replace("-", " ")
        if text:
            results.append({"slug": slug, "text": text})
    return results


# ------------------------------------------------------------------
# Episode list from anime page
# ------------------------------------------------------------------

async def scrape_series(slug: str) -> list:
    html = await fetch_text(f"{BASE}/anime/{slug}/", {"Referer": BASE})
    episodes = []
    # Episodes listed as: <div class="episodes-card-container"> or similar ul/li
    # witanime uses: <a href="/episode/SLUG-episode-N/">
    for m in re.finditer(
        r'<a\b[^>]*href=["\'](' + re.escape(BASE) + r'/episode/[^"\']+)["\'][^>]*>([\s\S]*?)</a>',
        html, re.IGNORECASE
    ):
        href = m.group(1).rstrip("/")
        label = strip_tags(m.group(2)).strip()
        # Extract episode number from URL or label
        ep_m = re.search(r'[-_](\d+)(?:[/_]|$)', href)
        if not ep_m:
            ep_m = re.search(r'(\d+)', label)
        if not ep_m:
            continue
        number = int(ep_m.group(1))
        ep_slug_m = re.search(r'/episode/([^/?#]+)', href)
        if not ep_slug_m:
            continue
        ep_slug = ep_slug_m.group(1)
        episodes.append({
            "number": number,
            "title": label or f"الحلقة {number}",
            "epSlug": ep_slug,
            "hasSub": True,  # witanime is Arabic-subbed
            "hasDub": False,
        })
    # Deduplicate and sort
    episodes.sort(key=lambda e: e["number"])
    seen, out = set(), []
    for e in episodes:
        if e["number"] not in seen:
            seen.add(e["number"])
            out.append(e)
    return out


# ------------------------------------------------------------------
# Extract streams from episode page
# ------------------------------------------------------------------

async def scrape_episode_streams(ep_slug: str) -> list:
    url = f"{BASE}/episode/{ep_slug}/"
    html = await fetch_text(url, {"Referer": BASE})

    streams = []

    # 1) Look for direct m3u8 / mp4 in page source
    for m in re.finditer(r'["\']((https?://[^"\']+\.(?:m3u8|mp4)[^"\']*?))["\']', html, re.IGNORECASE):
        link = m.group(1)
        quality = "1080p" if "1080" in link else ("720p" if "720" in link else "auto")
        streams.append({
            "url": link,
            "type": "hls" if ".m3u8" in link else "mp4",
            "quality": quality,
            "audio": LANG,
            "server": "witanime-direct",
            "referer": f"{BASE}/",
            "priority": 10,
            "isActive": len(streams) == 0,
        })

    # 2) Look for iframe src (embed players)
    for m in re.finditer(r'<iframe\b[^>]*src=["\']([^"\']+)["\']', html, re.IGNORECASE):
        embed_url = decode_entities(m.group(1))
        if not embed_url.startswith("http"):
            embed_url = BASE + embed_url if embed_url.startswith("/") else f"https:{embed_url}"
        streams.append({
            "url": embed_url,
            "type": "embed",
            "quality": "auto",
            "audio": LANG,
            "server": f"witanime-embed-{urlparse(embed_url).netloc}",
            "referer": f"{BASE}/",
            "priority": 5,
            "isActive": len(streams) == 0,
        })

    # 3) Look for jwplayer / source tags
    for m in re.finditer(r'file\s*:\s*["\']((https?://[^"\']+))["\']', html, re.IGNORECASE):
        link = m.group(1)
        if link not in {s["url"] for s in streams}:
            streams.append({
                "url": link,
                "type": "hls" if ".m3u8" in link else "mp4",
                "quality": "auto",
                "audio": LANG,
                "server": "witanime-jwplayer",
                "referer": f"{BASE}/",
                "priority": 8,
                "isActive": len(streams) == 0,
            })

    # 4) video-player data attributes
    for m in re.finditer(r'data-src=["\']([^"\']+)["\']', html, re.IGNORECASE):
        link = decode_entities(m.group(1))
        if "http" in link and link not in {s["url"] for s in streams}:
            streams.append({
                "url": link,
                "type": "embed" if ".html" in link else "mp4",
                "quality": "auto",
                "audio": LANG,
                "server": "witanime-datasrc",
                "referer": f"{BASE}/",
                "priority": 6,
                "isActive": len(streams) == 0,
            })

    return streams


# ------------------------------------------------------------------
# Provider interface (matches what _race.py expects)
# ------------------------------------------------------------------

async def search_fn(query: str) -> list:
    return await search(query)


async def resolve_series(anilist_id: int, ctx: dict | None = None) -> dict:
    ctx = ctx or await build_ctx(anilist_id)
    key = f"np:{NAME}:{anilist_id}"
    hit = _cache.cached(key, _cache.SHOW_IDENTITY_TTL)
    if hit is not None:
        return hit

    media = ctx["media"]
    titles = build_titles(media, ctx.get("anizip"))
    candidates = await find_top_slugs(titles, search_fn)
    expected = expected_count(media, ctx.get("anizip"))
    try:
        offset = await get_prequel_offset(anilist_id)
    except Exception:
        offset = 0

    is_single_movie = str((media or {}).get("format") or "").upper() == "MOVIE" or expected == 1
    selected = await select_series(
        candidates, scrape_series, expected,
        media.get("status"), offset,
        min_score=0.9 if is_single_movie else 0.6
    )
    if not selected:
        raise RuntimeError(f"witanime: no match found for AniList {anilist_id}")

    data = {
        "slug": selected["slug"],
        "title": selected["title"],
        "mode": selected["mode"],
        "offset": offset,
        "score": selected["score"],
    }
    _cache.set(key, data, _cache.SHOW_IDENTITY_TTL)
    return data


def _build_lists(anilist_id: int, series: dict, provider_eps: list, ctx: dict, expected) -> dict:
    sub = []
    for src in provider_eps:
        number = src["number"] - series["offset"] if series["mode"] == "offset" else src["number"]
        if number < 1:
            continue
        if expected and number > expected:
            continue
        meta = episode_meta(number, ctx)
        base = {
            "number": number,
            "title": meta["title"] or src.get("title") or f"الحلقة {number}",
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
    found = next((e for e in episodes if e["number"] == provider_ep), None)
    if not found:
        raise RuntimeError(f"witanime: episode {provider_ep} not found for AniList {anilist_id}")
    streams = await scrape_episode_streams(found["epSlug"])
    if not streams:
        raise RuntimeError(f"witanime: no streams found for episode {provider_ep}")
    return streams
