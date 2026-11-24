
import asyncio
import base64
import hashlib
import html
import json
import re
import threading
import urllib.parse
from urllib.parse import urlparse

from curl_cffi import requests

try:
    from Cryptodome.Cipher import AES
    from Cryptodome.Util.Padding import pad, unpad
except ImportError:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad, unpad

from src.providers import _cache
from src.providers._media import build_ctx
from src.providers._match import (
    build_titles,
    decode_entities,
    episode_meta,
    expected_count,
    find_top_slugs,
    get_prequel_offset,
    select_series,
)


NAME = "anime_slayer"

BASE_URL = "https://anslayer.com/anime/public/"
MULTI_ENDPOINTS = [
    "https://anslayer.com/la/public/api/f2",
    "https://anslayer.com/la/public/api/f",
    "https://a-reslayer.com/la/public/api/f",
    "https://a-reslayer.com/la/public/api/f2",
]

CLIENT_ID = "android-app2"
CLIENT_SECRET = "7befba6263cc14c90d2f1d6da2c5cf9b251bfbbd"
APK_CERT_SHA1 = "44D8B79265DDBB9C887320F64521A76D72F6D7D4"
RNC_PASSWORD = b"android-app9>E>VBa=X%;[5BX~=Q~K"

HEADERS = {
    "Client-Id": CLIENT_ID,
    "Client-Secret": CLIENT_SECRET,
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
}

_local = threading.local()

HLS_RE = re.compile(
    r"""["'](https?://[^"']+\.m3u8(?:\?[^"']*)?)["']""",
    re.IGNORECASE,
)


def _session():
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session(
            impersonate="chrome120",
            timeout=12,
        )
        _local.session = session
    return session


def _api_get(endpoint, params=None):
    url = endpoint if endpoint.startswith("http") else BASE_URL + endpoint

    try:
        response = _session().get(
            url,
            params=params,
            headers=HEADERS,
            timeout=12,
        )
        if response.status_code != 200:
            print(
                f"[ANIME_SLAYER] API HTTP {response.status_code}: {url}",
                flush=True,
            )
            return None

        return response.json()

    except Exception as exc:
        print(
            f"[ANIME_SLAYER] API error: {type(exc).__name__}: {exc}",
            flush=True,
        )
        return None


def _api_post(endpoint, data=None):
    url = endpoint if endpoint.startswith("http") else BASE_URL + endpoint

    try:
        response = _session().post(
            url,
            data=data,
            headers={
                **HEADERS,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=12,
        )

        if response.status_code != 200:
            return None

        return response.json()

    except Exception as exc:
        print(
            f"[ANIME_SLAYER] POST error: {type(exc).__name__}: {exc}",
            flush=True,
        )
        return None


def _response_items(data):
    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    response = data.get("response")

    if isinstance(response, list):
        return response

    if isinstance(response, dict):
        items = response.get("data", [])
        return items if isinstance(items, list) else []

    items = data.get("data", [])
    return items if isinstance(items, list) else []


def _search_sync(query):
    payload = {
        "list_type": "anime_list",
        "anime_name": query,
        "limit": 30,
    }

    data = _api_get(
        "animes/get-published-animes",
        params={"json": json.dumps(payload, ensure_ascii=False)},
    )

    results = []
    seen = set()

    for item in _response_items(data):
        if not isinstance(item, dict):
            continue

        anime_id = str(item.get("anime_id") or "").strip()
        if not anime_id or anime_id in seen:
            continue

        seen.add(anime_id)

        title = (
            item.get("anime_english_title")
            or item.get("anime_name")
            or anime_id
        )

        results.append({
            "slug": anime_id,
            "text": str(title),
        })

    return results


async def search(query):
    query = (query or "").strip()
    if not query:
        return []

    return await asyncio.to_thread(_search_sync, query)


def _details_sync(anime_id):
    return _api_get(
        "anime/get-anime-details",
        params={
            "anime_id": str(anime_id),
            "fetch_episodes": "Yes",
            "more_info": "Yes",
        },
    )


def _parse_episode_number(value):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        match = re.search(r"\d+", str(value or ""))
        return int(match.group(0)) if match else 0


def _clean_episode_title(value, number):
    title = str(value or "").strip()

    title = re.sub(
        r"^(الحلقة|الحلقه|Episode|Ep)\s*[:\s\-]*\d+\s*[:\s\-]*",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()

    if title == str(number) or re.fullmatch(r"\d+", title):
        return ""

    return title


def _parse_episodes(details):
    if not isinstance(details, dict):
        return []

    raw = details.get("episodes")

    if isinstance(raw, dict):
        items = raw.get("data", []) or []
    elif isinstance(raw, list):
        items = raw
    else:
        items = []

    episodes = []

    for item in items:
        if not isinstance(item, dict):
            continue

        number = _parse_episode_number(item.get("episode_number"))
        if number < 1:
            continue

        servers = item.get("episode_urls") or []
        if isinstance(servers, dict):
            servers = [servers]

        episodes.append({
            "number": number,
            "title": _clean_episode_title(
                item.get("episode_name"),
                number,
            ) or f"Episode {number}",
            "servers": servers,
        })

    episodes.sort(key=lambda item: item["number"])

    unique = []
    seen = set()

    for episode in episodes:
        if episode["number"] in seen:
            continue
        seen.add(episode["number"])
        unique.append(episode)

    return unique


async def scrape_series(slug):
    data = await asyncio.to_thread(_details_sync, str(slug))

    if not isinstance(data, dict):
        return []

    details = data.get("response")
    if not isinstance(details, dict):
        return []

    return _parse_episodes(details)


def _resolve_cdn_sync(cdn_url):
    results = []

    try:
        parsed = urlparse(cdn_url)
        params = urllib.parse.parse_qs(parsed.query)

        f_value = params.get("f", [""])[0]
        e_value = params.get("e", [""])[0]

        if not f_value or not e_value:
            return results

        session = _session()

        seed_response = session.get(
            BASE_URL + "google.php",
            headers=HEADERS,
            timeout=8,
        )

        if seed_response.status_code != 200:
            return results

        seed = seed_response.text.strip()
        if len(seed) < 10:
            return results

        key_string = (seed[:10] + APK_CERT_SHA1[:22])[:16]
        key = key_string.encode("utf-8")

        native_payload = json.dumps(
            {
                "uhy": "com.anslayer",
                "dma": 47,
                "mvd": "1.5.10",
                "vko": APK_CERT_SHA1,
            },
            separators=(",", ":"),
        )

        cipher = AES.new(key, AES.MODE_ECB)
        encrypted = cipher.encrypt(
            pad(native_payload.encode("utf-8"), 16)
        )

        inf = json.dumps(
            {
                "a": base64.b64encode(encrypted).decode("utf-8"),
                "b": seed,
            },
            separators=(",", ":"),
        )

        response = session.post(
            BASE_URL + "v-qs.php",
            data={
                "f": f_value,
                "e": e_value,
                "inf": inf,
            },
            headers={
                **HEADERS,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=10,
        )

        if response.status_code != 200 or not response.text.strip():
            return results

        envelope = base64.b64decode(response.text.strip())

        if len(envelope) <= 66:
            return results

        salt = envelope[2:10]
        iv = envelope[18:34]
        ciphertext = envelope[34:-32]

        if not ciphertext or len(ciphertext) % 16:
            return results

        encryption_key = hashlib.pbkdf2_hmac(
            "sha1",
            RNC_PASSWORD,
            salt,
            10000,
            32,
        )

        aes = AES.new(encryption_key, AES.MODE_CBC, iv)
        plaintext = unpad(aes.decrypt(ciphertext), 16)

        stream_list = json.loads(plaintext.decode("utf-8"))

        if not isinstance(stream_list, list):
            return results

        for item in stream_list:
            if not isinstance(item, dict):
                continue

            url = item.get("file") or ""
            if not url.startswith("http"):
                continue

            lower = url.lower()

            if "hh.mp4" in lower or "h.mp4" in lower:
                resolution = "1080p"
            elif "s.mp4" in lower:
                resolution = "720p"
            elif "m.mp4" in lower:
                resolution = "480p"
            else:
                resolution = _detect_resolution(url)

            results.append({
                "url": url,
                "type": "hls" if ".m3u8" in lower else "mp4",
                "resolution": resolution,
                "server": "Anime Slayer CDN",
            })

    except Exception as exc:
        print(
            f"[ANIME_SLAYER] CDN extraction failed: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

    return results


def _detect_resolution(url, default="720p"):
    value = (url or "").lower()

    if "1080" in value or "fhd" in value or "hh.mp4" in value:
        return "1080p"
    if "720" in value or re.search(r"[/_.-]h\.mp4", value):
        return "720p"
    if "480" in value or re.search(r"[/_.-]s\.mp4", value):
        return "480p"
    if "360" in value or re.search(r"[/_.-]m\.mp4", value):
        return "360p"

    return default


def _canonical_key(url):
    value = (url or "").strip().lower()

    patterns = [
        (r"mediafire\.com/(?:file/)?([a-z0-9]+)", "mediafire"),
        (r"drive\.google\.com/(?:file/d/|open\?id=|uc\?id=)([a-z0-9_-]+)", "gdrive"),
        (r"ok\.ru/(?:video/|videoembed/)(\d+)", "okru"),
        (r"mp4upload\.com/(?:embed-)?([a-z0-9]+)", "mp4upload"),
        (r"goodstream\.one/video/embed/([a-z0-9]+)", "goodstream"),
    ]

    for pattern, name in patterns:
        match = re.search(pattern, value)
        if match:
            return f"{name}:{match.group(1)}"

    return value.rstrip("/")


def _extract_mediafire(url):
    try:
        response = _session().get(url, timeout=8)
        if response.status_code != 200:
            return None

        match = re.search(
            r'<a[^>]+id=["\']downloadButton["\'][^>]+href=["\']([^"\']+)',
            response.text,
            re.IGNORECASE,
        )

        if match:
            return html.unescape(match.group(1))

        match = re.search(
            r'href=["\'](https?://download[^"\']+)',
            response.text,
            re.IGNORECASE,
        )

        return html.unescape(match.group(1)) if match else None

    except Exception:
        return None


def _extract_gdrive(url):
    match = (
        re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
        or re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
    )

    if not match:
        return None

    file_id = match.group(1)
    direct = (
        "https://drive.google.com/uc"
        f"?export=download&id={file_id}"
    )

    try:
        response = _session().get(
            direct,
            headers=HEADERS,
            timeout=8,
            allow_redirects=True,
        )

        content_type = response.headers.get("Content-Type", "").lower()

        if response.status_code in (200, 206):
            if "video" in content_type or "octet-stream" in content_type:
                return response.url

        if response.status_code == 200 and "drive.google.com" in response.url:
            return direct

    except Exception:
        pass

    return None


def _extract_mp4upload(url):
    try:
        response = _session().get(url, timeout=8)
        if response.status_code != 200:
            return None

        patterns = [
            r'player\.src\(\{\s*type:\s*["\']video/mp4["\'],\s*src:\s*["\'](https?://[^"\']+)',
            r'src:\s*["\'](https?://[^"\']+\.mp4[^"\']*)',
        ]

        for pattern in patterns:
            match = re.search(pattern, response.text, re.IGNORECASE)
            if match:
                return html.unescape(match.group(1))

    except Exception:
        pass

    return None


def _extract_hls(url):
    try:
        response = _session().get(url, timeout=8)
        if response.status_code != 200:
            return None

        match = HLS_RE.search(response.text)
        return html.unescape(match.group(1)) if match else None

    except Exception:
        return None


def _extract_okru(url):
    match = re.search(r"/video/(\d+)", url)
    if not match:
        return None

    try:
        response = _session().get(
            f"https://ok.ru/videoembed/{match.group(1)}",
            timeout=8,
        )

        if response.status_code != 200:
            return None

        match = re.search(
            r'data-options=["\'](\{.*?\})["\']',
            response.text,
        )

        if not match:
            return None

        data = json.loads(html.unescape(match.group(1)))
        videos = (
            data.get("videos")
            or data.get("flashvars", {}).get("metadata", {}).get("videos", [])
        )

        if videos:
            return videos[-1].get("url")

    except Exception:
        pass

    return None


def _multi_servers_sync(multi_url):
    results = []

    parsed = urlparse(multi_url)
    params = urllib.parse.parse_qs(parsed.query)
    n_value = params.get("n", [""])[0]

    if not n_value:
        return results

    all_links = set()
    session = _session()

    for endpoint in MULTI_ENDPOINTS:
        try:
            response = session.get(
                endpoint,
                params={"n": n_value},
                headers=HEADERS,
                timeout=7,
            )

            if response.status_code != 200:
                continue

            data = response.json()

            if isinstance(data, list):
                for item in data:
                    if isinstance(item, str) and item.startswith("http"):
                        all_links.add(item)

        except Exception:
            continue

    seen = set()

    for link in all_links:
        key = _canonical_key(link)

        if key in seen:
            continue

        seen.add(key)

        lower = link.lower()
        direct = None
        server = "Anime Slayer"

        if "mediafire.com" in lower:
            direct = _extract_mediafire(link)
            server = "MediaFire"

        elif "drive.google.com" in lower:
            direct = _extract_gdrive(link)
            server = "Google Drive"

        elif "mp4upload.com" in lower:
            direct = _extract_mp4upload(link) or link
            server = "Mp4Upload"

        elif "ok.ru" in lower:
            direct = _extract_okru(link) or link
            server = "OK.ru"

        elif "goodstream" in lower:
            direct = _extract_hls(link) or link
            server = "Goodstream"

        elif any(host in lower for host in (
            "streamtape",
            "mixdrop",
            "uptostream",
            "vinovo",
            "dood",
            "filemoon",
            "pixeldrain",
            "lulustream",
            "vidmoly",
            "streamwish",
            "filelions",
        )):
            direct = link
            server = urlparse(link).netloc

        if not direct:
            continue

        resolution = _detect_resolution(direct)

        results.append({
            "url": direct,
            "type": (
                "hls" if ".m3u8" in direct.lower()
                else "mp4" if ".mp4" in direct.lower()
                else "embed"
            ),
            "resolution": resolution,
            "server": server,
        })

    return results


async def _extract_episode_streams(episode):
    servers = episode.get("servers") or []
    cdn_jobs = []
    multi_jobs = []

    for server in servers:
        if not isinstance(server, dict):
            continue

        name = str(server.get("episode_server_name") or "").lower().strip()
        url = str(server.get("episode_url") or "").strip()

        if not url:
            continue

        if name == "cdn" or "vq.php" in url or "v-qs.php" in url:
            cdn_jobs.append(
                asyncio.to_thread(_resolve_cdn_sync, url)
            )

        elif name == "muilt" or "/api/f" in url:
            multi_jobs.append(
                asyncio.to_thread(_multi_servers_sync, url)
            )

        elif url.startswith("http"):
            multi_jobs.append(
                asyncio.sleep(
                    0,
                    result=[{
                        "url": url,
                        "type": "embed",
                        "resolution": "Auto",
                        "server": name or "Anime Slayer",
                    }],
                )
            )

    groups = await asyncio.gather(
        *(cdn_jobs + multi_jobs),
        return_exceptions=True,
    )

    streams = []
    seen = set()

    for group in groups:
        if isinstance(group, Exception):
            continue

        for item in group or []:
            url = item.get("url")
            if not url or url in seen:
                continue

            seen.add(url)
            streams.append(item)

    rank = {
        "1080p": 4,
        "720p": 3,
        "480p": 2,
        "360p": 1,
        "Auto": 0,
    }

    streams.sort(
        key=lambda item: rank.get(item.get("resolution"), 0),
        reverse=True,
    )

    return streams


async def resolve_series(anilist_id, ctx=None):
    ctx = ctx or await build_ctx(anilist_id)

    key = f"np:anime_slayer:{anilist_id}"
    hit = _cache.cached(key, _cache.SHOW_IDENTITY_TTL)

    if hit is not None:
        return hit

    media = ctx["media"]
    titles = build_titles(media, ctx.get("anizip"))

    candidates = await find_top_slugs(titles, search)

    if not candidates:
        raise RuntimeError(
            f"Anime Slayer search found no results for AniList {anilist_id}"
        )

    expected = expected_count(media, ctx.get("anizip"))

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
            f"Anime Slayer match not confident for AniList {anilist_id}"
        )

    result = {
        "slug": selected["slug"],
        "title": selected["title"],
        "mode": selected["mode"],
        "offset": offset,
        "score": selected["score"],
    }

    _cache.set(key, result, _cache.SHOW_IDENTITY_TTL)
    return result


def _build_episode_lists(anilist_id, series, provider_eps, ctx, expected):
    sub = []

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

        sub.append({
            "id": f"watch/anime_slayer/{anilist_id}/sub/anime-slayer-{number}",
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
            "audio": "sub",
        })

    return {
        "sub": sub,
        "dub": [],
    }


async def get_episodes(anilist_id, ctx=None):
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
            "matchScore": round(series["score"], 3),
            "numbering": series["mode"],
            "episodeOffset": (
                series["offset"]
                if series["mode"] == "offset"
                else 0
            ),
        },
        "episodes": _build_episode_lists(
            anilist_id,
            series,
            episodes,
            ctx,
            expected,
        ),
    }


async def watch(anilist_id, audio, ep, ctx=None):
    if audio == "dub":
        return []

    ctx = ctx or await build_ctx(anilist_id)

    series = await resolve_series(anilist_id, ctx)

    provider_number = (
        int(ep) + series["offset"]
        if series["mode"] == "offset"
        else int(ep)
    )

    episodes = await scrape_series(series["slug"])

    target = next(
        (
            item for item in episodes
            if item["number"] == provider_number
        ),
        None,
    )

    if target is None:
        raise RuntimeError(
            f"Anime Slayer episode {provider_number} not found "
            f"for AniList {anilist_id}"
        )

    extracted = await _extract_episode_streams(target)

    streams = []

    for index, item in enumerate(extracted):
        url = item["url"]
        stream_type = item["type"]

        parsed = urlparse(url)
        referer = (
            f"{parsed.scheme}://{parsed.netloc}/"
            if parsed.scheme and parsed.netloc
            else BASE_URL
        )

        streams.append({
            "url": url,
            "type": stream_type,
            "embed": url if stream_type == "embed" else None,
            "audio": "sub",
            "server": item.get("server") or "Anime Slayer",
            "quality": item.get("resolution") or "Auto",
            "priority": len(extracted) - index,
            "referer": referer,
            "isActive": index == 0,
        })

    if not streams:
        raise RuntimeError(
            f"Anime Slayer returned no playable streams "
            f"(AniList={anilist_id}, episode={ep})"
        )

    return streams
