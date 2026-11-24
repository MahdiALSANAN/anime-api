
"""
Anime3rb Arabic provider for ACEanime.

Uses curl_cffi for browser TLS impersonation and BeautifulSoup
for parsing Anime3rb pages.
"""

import asyncio
import json
import re
import threading
from urllib.parse import quote, urljoin, urlparse

from curl_cffi import requests
from bs4 import BeautifulSoup

from src.providers import _cache
from src.providers._match import (
    build_titles,
    decode_entities,
    episode_meta,
    expected_count,
    find_top_slugs,
    get_prequel_offset,
    select_series,
)
from src.providers._media import build_ctx


NAME = "anime3rb"
BASE = "https://anime3rb.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
}

_session = None
_session_lock = threading.RLock()
_context_lock = threading.RLock()

_lw_token = None
_lw_snapshot = None
_lw_update_uri = None
_lw_time = 0


def _get_session():
    global _session

    with _session_lock:
        if _session is None:
            _session = requests.Session(
                impersonate="chrome120",
                timeout=15,
            )

        return _session


def _get_livewire_context(force=False):
    global _lw_token, _lw_snapshot, _lw_update_uri, _lw_time

    with _context_lock:
        import time

        if (
            not force
            and _lw_token
            and _lw_snapshot
            and _lw_update_uri
            and time.time() - _lw_time < 1800
        ):
            return _lw_token, _lw_snapshot, _lw_update_uri

        session = _get_session()
        response = session.get(
            BASE,
            headers=HEADERS,
            timeout=10,
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Anime3rb homepage returned HTTP {response.status_code}"
            )

        html = response.text

        token_match = re.search(
            r'data-csrf="([^"]+)"',
            html,
        )

        if not token_match:
            token_match = re.search(
                r'csrf-token.*?content="([^"]+)"',
                html,
            )

        _lw_token = token_match.group(1) if token_match else ""

        _lw_snapshot = None

        for match in re.finditer(
            r'wire:snapshot=(["\'])([^"\']+?)\1',
            html,
        ):
            snapshot = (
                match.group(2)
                .replace("&quot;", '"')
                .replace("&amp;", "&")
            )

            if '"name":"search"' in snapshot:
                _lw_snapshot = snapshot
                break

        update_match = re.search(
            r'data-update-uri="([^"]+)"',
            html,
        )

        if update_match:
            _lw_update_uri = update_match.group(1)
        else:
            _lw_update_uri = f"{BASE}/livewire/update"

        _lw_time = time.time()

        if not _lw_snapshot:
            raise RuntimeError(
                "Anime3rb Livewire search context not found"
            )

        return _lw_token, _lw_snapshot, _lw_update_uri


def _parse_search(html):
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen = set()

    for card in soup.find_all(
        "a",
        href=re.compile(r"/titles/([^/]+)$"),
    ):
        href = card.get("href", "")
        match = re.search(r"/titles/([^/]+)$", href)

        if not match:
            continue

        slug = match.group(1)

        if slug in seen:
            continue

        seen.add(slug)

        title_node = card.find(["h4", "h3", "h2", "span", "p"])
        title = (
            title_node.get_text(" ", strip=True)
            if title_node
            else slug.replace("-", " ").title()
        )

        results.append({
            "slug": slug,
            "text": title,
        })

    return results


def _search_sync(query):
    session = _get_session()

    try:
        token, snapshot, update_uri = _get_livewire_context()

        payload = {
            "_token": token,
            "components": [{
                "snapshot": snapshot,
                "updates": {
                    "query": query,
                    "deep": False,
                },
                "calls": [],
            }],
        }

        headers = {
            **HEADERS,
            "Referer": BASE + "/",
            "X-Livewire": "true",
            "X-CSRF-TOKEN": token,
            "Content-Type": "application/json",
        }

        response = session.post(
            update_uri,
            json=payload,
            headers=headers,
            timeout=10,
        )

        if response.status_code in (404, 419):
            token, snapshot, update_uri = _get_livewire_context(
                force=True
            )

            payload["_token"] = token
            payload["components"][0]["snapshot"] = snapshot

            headers["X-CSRF-TOKEN"] = token

            response = session.post(
                update_uri,
                json=payload,
                headers=headers,
                timeout=10,
            )

        if response.status_code == 200:
            data = response.json()
            component = (data.get("components") or [{}])[0]
            html = (component.get("effects") or {}).get("html", "")

            if html:
                results = _parse_search(html)

                if results:
                    return results

    except Exception as exc:
        print(
            f"[ANIME3RB] Livewire search failed: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

    # HTML search fallback
    try:
        response = session.get(
            f"{BASE}/titles?q={quote(query)}",
            headers=HEADERS,
            timeout=10,
        )

        if response.status_code == 200:
            results = _parse_search(response.text)
            words = [
                word.lower()
                for word in query.split()
                if len(word) >= 2
            ]

            return [
                item for item in results
                if any(
                    word in item["text"].lower()
                    or word in item["slug"].lower()
                    for word in words
                )
            ]

    except Exception as exc:
        print(
            f"[ANIME3RB] Search fallback failed: {exc}",
            flush=True,
        )

    return []


async def search(query: str) -> list:
    if not query or not query.strip():
        return []

    with _session_lock:
        return await asyncio.to_thread(
            _search_sync,
            query.strip(),
        )


def _scrape_series_sync(slug):
    session = _get_session()

    response = session.get(
        f"{BASE}/titles/{slug}",
        headers=HEADERS,
        timeout=15,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Anime3rb series returned HTTP {response.status_code}"
        )

    soup = BeautifulSoup(response.text, "html.parser")
    episodes = []
    seen = set()

    for link in soup.find_all(
        "a",
        href=re.compile(
            rf"/episode/{re.escape(slug)}/\d+"
        ),
    ):
        href = link.get("href", "")

        match = re.search(
            r"/episode/[^/]+/(\d+)",
            href,
        )

        if not match:
            continue

        number = int(match.group(1))

        if number in seen:
            continue

        seen.add(number)

        title_node = link.find("p")
        title = (
            title_node.get_text(" ", strip=True)
            if title_node
            else f"الحلقة {number}"
        )

        text = link.get_text(" ", strip=True).lower()

        episodes.append({
            "number": number,
            "title": title,
            "epSlug": f"ep-{number}",
            "hasSub": True,
            "hasDub": "مدبلج" in text or "dub" in text,
        })

    episodes.sort(key=lambda item: item["number"])
    return episodes


async def scrape_series(slug: str) -> list:
    with _session_lock:
        return await asyncio.to_thread(
            _scrape_series_sync,
            slug,
        )


def _extract_player_url(html):
    for match in re.finditer(
        r'wire:snapshot=(["\'])([^"\']+?)\1',
        html,
    ):
        try:
            raw = (
                match.group(2)
                .replace("&quot;", '"')
                .replace("&amp;", "&")
            )

            data = json.loads(raw)
            video_url = data.get("data", {}).get("video_url")

            if video_url and "vid3rb" in video_url:
                return video_url.replace("\\/", "/")

        except Exception:
            pass

    match = re.search(
        r'https?://[a-zA-Z0-9.-]*vid3rb\.com/player/'
        r'[a-zA-Z0-9_-]+(?:\?[^"\'<>\s]+)?',
        html,
    )

    if match:
        return match.group(0).replace("&amp;", "&")

    soup = BeautifulSoup(html, "html.parser")

    for iframe in soup.find_all("iframe"):
        src = (
            iframe.get("src")
            or iframe.get("data-src")
            or ""
        )

        if "vid3rb.com/player/" in src:
            return decode_entities(src)

    return None


def _extract_sources(html):
    patterns = [
        r'(?:video_sources\s*=\s*)?(\[\s*\{.*?\}\s*\])',
        r'(\[\s*\{[^{}]*?"src"[^{}]*?\}\s*'
        r'(?:,\s*\{[^{}]*?\}\s*)*\])',
    ]

    for pattern in patterns:
        for raw in re.findall(pattern, html, re.DOTALL):
            try:
                data = json.loads(
                    raw.replace("\\/", "/")
                )

                if isinstance(data, list):
                    valid = [
                        item for item in data
                        if isinstance(item, dict)
                        and item.get("src")
                    ]

                    if valid:
                        return valid

            except Exception:
                pass

    urls = re.findall(
        r'https?://[a-zA-Z0-9.-]*vid3rb\.com/video/'
        r'[a-zA-Z0-9_-]+(?:\?[^"\'<>\s]+)?',
        html,
    )

    return [
        {
            "src": url.replace("\\/", "/").replace("&amp;", "&"),
            "label": "Direct Stream",
            "res": "",
            "premium": False,
        }
        for url in dict.fromkeys(urls)
    ]


def _resolve_stream_sync(slug, episode):
    session = _get_session()

    episode_url = f"{BASE}/episode/{slug}/{episode}"

    response = session.get(
        episode_url,
        headers=HEADERS,
        timeout=12,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Episode page returned HTTP {response.status_code}"
        )

    player_url = _extract_player_url(response.text)

    if not player_url:
        raise RuntimeError("Anime3rb player URL not found")

    player_headers = {
        **HEADERS,
        "Referer": BASE + "/",
    }

    player_response = session.get(
        player_url,
        headers=player_headers,
        timeout=12,
    )

    if player_response.status_code != 200:
        raise RuntimeError(
            f"Vid3rb player returned HTTP {player_response.status_code}"
        )

    sources = _extract_sources(player_response.text)

    if not sources:
        raise RuntimeError("No video sources found in player")

    streams = []

    for item in sources:
        if item.get("premium"):
            continue

        raw_url = item.get("src")

        if not raw_url:
            continue

        direct_url = raw_url

        try:
            redirect = session.get(
                raw_url,
                headers={
                    **HEADERS,
                    "Referer": player_url,
                },
                allow_redirects=False,
                timeout=6,
            )

            if redirect.status_code in (301, 302, 307, 308):
                direct_url = urljoin(
                    raw_url,
                    redirect.headers.get("Location", raw_url),
                )

        except Exception:
            pass

        resolution = str(item.get("res") or "")
        label = str(item.get("label") or "Anime3rb")

        if "1080" in resolution or "1080" in label:
            quality = "1080p"
        elif "720" in resolution or "720" in label:
            quality = "720p"
        elif "480" in resolution or "480" in label:
            quality = "480p"
        else:
            quality = resolution or label

        parsed = urlparse(player_url)
        referer = f"{parsed.scheme}://{parsed.netloc}/"

        streams.append({
            "url": direct_url,
            "type": (
                "hls"
                if ".m3u8" in direct_url.lower()
                else "mp4"
            ),
            "embed": player_url,
            "audio": "sub",
            "server": f"Anime3rb {quality}",
            "quality": quality,
            "priority": 1,
            "referer": referer,
            "headers": {
                "Referer": referer,
                "User-Agent": HEADERS["User-Agent"],
            },
            "isActive": not streams,
        })

    if not streams:
        raise RuntimeError("No playable non-premium streams")

    return streams


async def scrape_episode_watch(
    series_slug: str,
    ep_slug: str,
    audio: str,
) -> list:
    match = re.search(r"\d+", str(ep_slug))

    if not match:
        raise RuntimeError(
            f"Invalid Anime3rb episode number: {ep_slug}"
        )

    episode = int(match.group(0))

    with _session_lock:
        streams = await asyncio.to_thread(
            _resolve_stream_sync,
            series_slug,
            episode,
        )

    for stream in streams:
        stream["audio"] = audio

    return streams


async def resolve_series(
    anilist_id: int,
    ctx: dict | None = None,
) -> dict:
    ctx = ctx or await build_ctx(anilist_id)

    key = f"np:anime3rb:{anilist_id}"

    hit = _cache.cached(
        key,
        _cache.SHOW_IDENTITY_TTL,
    )

    if hit is not None:
        return hit

    media = ctx["media"]

    titles = build_titles(
        media,
        ctx.get("anizip"),
    )

    candidates = await find_top_slugs(
        titles,
        search,
    )

    expected = expected_count(
        media,
        ctx.get("anizip"),
    )

    try:
        offset = await get_prequel_offset(anilist_id)
    except Exception:
        offset = 0

    selected = await select_series(
        candidates,
        scrape_series,
        expected,
        media.get("status"),
        offset,
    )

    if not selected:
        raise RuntimeError(
            f"Anime3rb match not found for AniList {anilist_id}"
        )

    data = {
        "slug": selected["slug"],
        "title": selected["title"],
        "mode": selected["mode"],
        "offset": offset,
        "score": selected["score"],
    }

    _cache.set(
        key,
        data,
        _cache.SHOW_IDENTITY_TTL,
    )

    return data


def _build_lists(
    anilist_id: int,
    series: dict,
    provider_eps: list,
    ctx: dict,
    expected,
) -> dict:
    sub = []
    dub = []

    for source in provider_eps:
        number = (
            source["number"] - series["offset"]
            if series["mode"] == "offset"
            else source["number"]
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
                or source.get("title")
                or f"Episode {number}"
            ),
            "duration": meta["duration"],
            "filler": meta["filler"],
            "uncensored": meta["uncensored"],
            "description": meta["description"],
            "image": meta["image"],
            "airDate": meta["airDate"],
            "sourceNumber": source["number"],
        }

        if source.get("hasSub"):
            sub.append({
                "id": (
                    f"watch/anime3rb/{anilist_id}/"
                    f"sub/anime3rb-{number}"
                ),
                **base,
                "audio": "sub",
            })

        if source.get("hasDub"):
            dub.append({
                "id": (
                    f"watch/anime3rb/{anilist_id}/"
                    f"dub/anime3rb-{number}"
                ),
                **base,
                "audio": "dub",
            })

    return {
        "sub": sub,
        "dub": dub,
    }


async def get_episodes(
    anilist_id: int,
    ctx: dict | None = None,
) -> dict:
    ctx = ctx or await build_ctx(anilist_id)

    media = ctx["media"]
    series = await resolve_series(anilist_id, ctx)

    provider_eps = await scrape_series(series["slug"])

    expected = expected_count(
        media,
        ctx.get("anizip"),
    )

    return {
        "meta": {
            "id": series["slug"],
            "title": series["title"],
            "source": NAME,
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
            provider_eps,
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

    series = await resolve_series(
        anilist_id,
        ctx,
    )

    provider_ep = (
        int(ep) + series["offset"]
        if series["mode"] == "offset"
        else int(ep)
    )

    return await scrape_episode_watch(
        series["slug"],
        f"ep-{provider_ep}",
        audio,
    )
