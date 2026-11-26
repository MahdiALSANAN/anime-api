"""
anime4up.py  —  مزوّد Anime4Up العربي
الموقع: https://w1.anime4up.rest
اللغة: عربي (مترجم)
"""

from __future__ import annotations

import base64
import json
import re
import logging
from typing import Any

from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

from ._http import fetch_text
from ._match import find_top_slugs, select_series

log = logging.getLogger(__name__)

# ─── ثوابت ───────────────────────────────────────────────────────────────────
NAME = "anime4up"
BASE = "https://w1.anime4up.rest"
LANG = "ar"

# مفتاح AES وـ IV المستخدمان في تشفير روابط الخوادم
_AES_KEY = b"4^TT%zCMd4L4ma4Z"   # 16 byte
_AES_IV  = b"4^TT%zCMd4L4ma4Z"   # 16 byte

# ترتيب الجودة — الأعلى أولاً
_QUALITY_ORDER = {"1080": 0, "720": 1, "480": 2, "360": 3, "240": 4}


# ─── مساعدات HTTP ─────────────────────────────────────────────────────────────

async def _get(path: str, **kw) -> str:
    """جلب صفحة من الموقع مع headers مناسبة."""
    url = path if path.startswith("http") else f"{BASE}{path}"
    headers = {
        "Referer": BASE + "/",
        "Accept-Language": "ar,en;q=0.9",
    }
    return await fetch_text(url, headers=headers, **kw)


# ─── فكّ تشفير روابط الخوادم ─────────────────────────────────────────────────

def _decrypt_server_link(encoded: str) -> str | None:
    """
    يفكّ تشفير رابط الخادم المشفَّر بـ AES-CBC.
    """
    try:
        raw = base64.b64decode(encoded)
        cipher = AES.new(_AES_KEY, AES.MODE_CBC, _AES_IV)
        decrypted = unpad(cipher.decrypt(raw), AES.block_size)
        return decrypted.decode("utf-8").strip()
    except Exception as e:
        log.debug("anime4up: فشل فكّ التشفير: %s", e)
        return None


def _try_decode_base64(s: str) -> str:
    """يحاول فكّ base64 العادي إذا فشل AES."""
    try:
        return base64.b64decode(s).decode("utf-8").strip()
    except Exception:
        return s


# ─── استخراج روابط الفيديو من صفحة الحلقة ───────────────────────────────────

async def _extract_streams(ep_url: str) -> list[dict]:
    """
    يفتح صفحة الحلقة ويستخرج روابط HLS المباشرة.
    """
    streams: list[dict] = []

    try:
        html = await _get(ep_url)
    except Exception as e:
        log.warning("anime4up: فشل جلب صفحة الحلقة %s: %s", ep_url, e)
        return streams

    soup = BeautifulSoup(html, "html.parser")

    server_items = soup.select("ul.server-list li, ul.servers-list li, .watch-servers li")

    if not server_items:
        raw_videos = re.findall(r'data-video=["\']([^"\']+)["\']', html)
        for encoded in raw_videos:
            url = _resolve_video_url(encoded)
            if url:
                streams.extend(await _fetch_hls_from_embed(url))
        return _sort_streams(streams)

    for item in server_items:
        encoded = item.get("data-video") or item.get("data-src") or ""
        if not encoded:
            continue

        url = _resolve_video_url(encoded)
        if not url:
            continue

        new_streams = await _fetch_hls_from_embed(url)
        streams.extend(new_streams)

    return _sort_streams(streams)


def _resolve_video_url(encoded: str) -> str | None:
    """
    يحوّل القيمة المشفّرة إلى رابط iframe/embed حقيقي.
    """
    if not encoded:
        return None

    if encoded.startswith("http"):
        return encoded

    decrypted = _decrypt_server_link(encoded)
    if decrypted and decrypted.startswith("http"):
        return decrypted

    decoded = _try_decode_base64(encoded)
    if decoded.startswith("http"):
        return decoded

    return None


async def _fetch_hls_from_embed(embed_url: str) -> list[dict]:
    """
    يفتح صفحة الـ embed ويستخرج روابط HLS (.m3u8).
    """
    streams: list[dict] = []

    if not embed_url or not embed_url.startswith("http"):
        return streams

    try:
        html = await fetch_text(embed_url, headers={"Referer": BASE + "/"}, timeout=15.0)
    except Exception as e:
        log.debug("anime4up: فشل جلب embed %s: %s", embed_url, e)
        return streams

    hls_urls = re.findall(r'["\']([^"\']*\.m3u8[^"\']*)["\']', html)

    for hls_url in hls_urls:
        hls_url = hls_url.strip()
        if not hls_url.startswith("http"):
            continue

        quality = _guess_quality(hls_url)

        streams.append({
            "url": hls_url,
            "quality": quality,
            "server": _extract_server_name(embed_url),
            "type": "hls",
        })

    if not streams:
        json_matches = re.findall(
            r'sources\s*:\s*\[([^\]]+)\]', html, re.DOTALL
        )
        for match in json_matches:
            file_urls = re.findall(r'["\']file["\']\s*:\s*["\']([^"\']+)["\']', match)
            for url in file_urls:
                if url.startswith("http"):
                    quality = _guess_quality(url)
                    streams.append({
                        "url": url,
                        "quality": quality,
                        "server": _extract_server_name(embed_url),
                        "type": "hls",
                    })

        if not streams:
            file_matches = re.findall(r'["\']?file["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']', html)
            for url in file_matches:
                if url.startswith("http"):
                    streams.append({
                        "url": url,
                        "quality": _guess_quality(url),
                        "server": _extract_server_name(embed_url),
                        "type": "hls",
                    })

    return streams


def _guess_quality(url: str) -> str:
    """يخمّن الجودة من الرابط."""
    for q in ("1080", "720", "480", "360", "240"):
        if q in url:
            return f"{q}p"
    return "auto"


def _extract_server_name(embed_url: str) -> str:
    """يستخرج اسم الخادم من الرابط."""
    try:
        from urllib.parse import urlparse
        host = urlparse(embed_url).netloc
        return host.split(".")[0]
    except Exception:
        return "unknown"


def _sort_streams(streams: list[dict]) -> list[dict]:
    """يرتّب الروابط: 1080p ← 720p ← 480p ← بقية."""
    def _key(s: dict) -> int:
        q = s.get("quality", "auto").replace("p", "")
        return _QUALITY_ORDER.get(q, 99)
    return sorted(streams, key=_key)


# ─── البحث عن الأنمي ─────────────────────────────────────────────────────────

async def _search_site(query: str) -> list[dict]:
    """
    يبحث في Anime4Up ويعيد قائمة النتائج.
    """
    candidates: list[dict] = []

    try:
        search_url = f"{BASE}/?s={query.replace(' ', '+')}"
        html = await _get(search_url)
        soup = BeautifulSoup(html, "html.parser")

        cards = soup.select("div.anime-card-container, div.anime-card, article.anime-card")

        if not cards:
            cards = soup.select(".film-poster, .anime-block, .anime-item")

        for card in cards:
            link = card.select_one("a")
            title_el = card.select_one(".anime-card-title, h3, .title, h2")

            if not link:
                continue

            href = link.get("href", "")
            title = (title_el.get_text(strip=True) if title_el
                     else link.get("title", "") or link.get_text(strip=True))

            if not href or not title:
                continue

            slug = href.rstrip("/").split("/")[-1]

            candidates.append({
                "slug": slug,
                "title": title,
                "url": href if href.startswith("http") else f"{BASE}{href}",
            })

    except Exception as e:
        log.warning("anime4up: فشل البحث عن '%s': %s", query, e)

    return candidates


async def _search_fn(query: str) -> list[dict]:
    """
    دالة البحث التي تُمرَّر إلى find_top_slugs().
    """
    results = await _search_site(query)
    return [{"slug": r["slug"], "title": r["title"]} for r in results]


# ─── جلب قائمة الحلقات من صفحة الأنمي ───────────────────────────────────────

async def _scrape_anime_page(slug: str) -> dict | None:
    """
    يفتح صفحة الأنمي ويجمع العنوان وروابط الحلقات.
    """
    try:
        url = f"{BASE}/anime/{slug}/"
        html = await _get(url)
    except Exception as e:
        log.warning("anime4up: فشل جلب صفحة الأنمي '%s': %s", slug, e)
        return None

    soup = BeautifulSoup(html, "html.parser")

    title_ar = ""
    title_en = ""

    h1 = soup.select_one("h1.anime-title, h1, .anime-name")
    if h1:
        title_ar = h1.get_text(strip=True)

    title_en_el = soup.select_one(".anime-title-english, [lang='en']")
    if title_en_el:
        title_en = title_en_el.get_text(strip=True)

    ep_links: list[dict] = []

    ep_items = soup.select("div.episode-link a, ul.episodes-list a, .all-episodes a")

    if not ep_items:
        ep_items = soup.select("a[href*='/episode/']")

    for a in ep_items:
        href = a.get("href", "")
        if not href or "/episode/" not in href:
            continue

        ep_text = a.get_text(strip=True)
        ep_num = _extract_ep_number(ep_text, href)

        ep_links.append({
            "number": ep_num,
            "url": href if href.startswith("http") else f"{BASE}{href}",
        })

    if not ep_links:
        log.debug("anime4up: لم يُعثر على حلقات للأنمي '%s'", slug)
        return None

    ep_links.sort(key=lambda x: x["number"])

    return {
        "slug": slug,
        "title_ar": title_ar,
        "title_en": title_en,
        "episodes": ep_links,
        "total": len(ep_links),
    }


def _extract_ep_number(text: str, url: str) -> int:
    """يستخرج رقم الحلقة من النص أو الرابط."""
    m = re.search(r'(\d+)', text)
    if m:
        return int(m.group(1))

    m = re.search(r'[حة-](\d+)', url)
    if m:
        return int(m.group(1))

    nums = re.findall(r'\d+', url)
    if nums:
        return int(nums[-1])

    return 0


# ─── الدوال الرئيسية (الواجهة مع _race.py) ───────────────────────────────────

async def get_episodes(anilist_id: int, ctx: dict) -> dict:
    """
    يبحث عن الأنمي ويعيد قائمة الحلقات.
    ✅ episodes["sub"] = list of {"ep": N, "url": "..."} — يتوافق مع _race.py
    """
    titles: list[str] = ctx.get("titles", [])
    expected: int = ctx.get("expected_episodes", 0)
    status: str = ctx.get("status", "")
    offset: int = ctx.get("season_offset", 0)

    if not titles:
        log.warning("anime4up: لا توجد عناوين للبحث (anilist_id=%s)", anilist_id)
        return {}

    # ─ الخطوة 1: البحث الذكي عن slug ─
    top_slugs = await find_top_slugs(titles, _search_fn, n=6)

    if not top_slugs:
        log.info("anime4up: لم يُعثر على نتائج لـ anilist_id=%s", anilist_id)
        return {}

    # ─ الخطوة 2: اختيار أفضل تطابق بحسب عدد الحلقات ─
    series = await select_series(
        candidates=top_slugs,
        scrape_fn=_scrape_anime_page,
        expected=expected,
        status=status,
        offset=offset,
    )

    if not series:
        log.info("anime4up: لم يُعثر على تطابق مناسب لـ anilist_id=%s", anilist_id)
        return {}

    # ─ الخطوة 3: بناء قائمة الحلقات كـ list ─
    # ✅ FIXED: كان dict[int, str] — الآن list[{"ep": N, "url": "..."}]
    # _race.py يتحقق: isinstance(v, list) — dict يُتجاهل تماماً
    episodes_list: list[dict] = []
    for ep in series.get("episodes", []):
        num = ep["number"]
        if offset:
            num = num - offset
        if num > 0:
            episodes_list.append({"ep": num, "url": ep["url"]})

    if not episodes_list:
        return {}

    episodes_list.sort(key=lambda x: x["ep"])

    return {
        "meta": {
            "provider": NAME,
            "lang": LANG,
            "slug": series.get("slug", ""),
            "title": series.get("title_ar", "") or series.get("title_en", ""),
        },
        "episodes": {
            # ✅ list — يتوافق مع _race.py isinstance(v, list)
            "sub": episodes_list,
            "dub": [],
        },
    }


async def watch(anilist_id: int, audio: str, ep: int, ctx: dict) -> list[dict]:
    """
    يعيد روابط HLS للحلقة المطلوبة.
    audio: "sub" فقط (عربي مترجم)
    """
    if audio != "sub":
        log.debug("anime4up: audio='%s' غير مدعوم (فقط 'sub')", audio)
        return []

    # ✅ FIXED: البحث في list بدل dict
    cached_sub: list = ctx.get("episodes", {}).get("sub", [])
    ep_url: str | None = next((e["url"] for e in cached_sub if e["ep"] == ep), None)

    if not ep_url:
        log.debug("anime4up: الحلقة %s غير موجودة في cache، إعادة البحث...", ep)
        fresh = await get_episodes(anilist_id, ctx)
        # ✅ FIXED: نفس التعديل هنا
        fresh_sub = fresh.get("episodes", {}).get("sub", [])
        ep_url = next((e["url"] for e in fresh_sub if e["ep"] == ep), None)

    if not ep_url:
        log.info("anime4up: الحلقة %s غير موجودة لـ anilist_id=%s", ep, anilist_id)
        return []

    streams = await _extract_streams(ep_url)

    if not streams:
        log.info("anime4up: لم يُعثر على روابط فيديو للحلقة %s", ep)
    else:
        log.info("anime4up: ✅ وُجد %d رابط للحلقة %s (أفضل: %s)",
                 len(streams), ep, streams[0].get("quality", "?"))

    return streams
