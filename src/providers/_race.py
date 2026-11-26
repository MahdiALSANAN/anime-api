from __future__ import annotations
import asyncio
import importlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ترتيب الأولوية — الأول يُجرَّب أولاً
AR_RANKING = ["gogoanime", "anime4up"]
EN_RANKING = ["gogoanime"]

WATCH_TIMEOUT = 75.0


def _load_provider(name: str):
    """تحميل provider ديناميكياً من مجلد providers"""
    try:
        mod = importlib.import_module(f".{name}", package="src.providers")
        return mod
    except ModuleNotFoundError:
        logger.warning("[RACE] provider '%s' not found", name)
        return None
    except Exception as e:
        logger.warning("[RACE] failed to load provider '%s': %s", name, e)
        return None


async def _try_provider(name: str, anilist_id: int, audio: str, ep: int, ctx: dict) -> list[dict]:
    """يجرب provider واحد ويرجع streams أو قائمة فارغة"""
    mod = _load_provider(name)
    if mod is None:
        return []
    watch_fn = getattr(mod, "watch", None)
    if watch_fn is None:
        logger.warning("[RACE] provider '%s' has no watch()", name)
        return []
    try:
        streams = await asyncio.wait_for(
            watch_fn(anilist_id, audio, ep, ctx),
            timeout=WATCH_TIMEOUT,
        )
        if streams:
            logger.warning("[RACE] %s returned %d streams (AniList=%s, episode=%s, audio=%s)",
                           name, len(streams), anilist_id, ep, audio)
        else:
            logger.warning("[RACE] %s returned no streams (AniList=%s, episode=%s, audio=%s)",
                           name, anilist_id, ep, audio)
        return streams or []
    except asyncio.TimeoutError:
        logger.warning("[RACE] %s timed out (AniList=%s, episode=%s)", name, anilist_id, ep)
        return []
    except Exception as e:
        logger.warning("[RACE] %s error: %s (AniList=%s, episode=%s)", name, e, anilist_id, ep)
        return []


async def race_watch(anilist_id: int, audio: str, ep: int, ctx: dict) -> list[dict]:
    """
    يجرب providers بالترتيب حسب audio:
    - sub  → EN_RANKING
    - ar   → AR_RANKING
    يرجع أول نتيجة غير فارغة
    """
    if audio == "ar":
        ranking = AR_RANKING
        label = "Arabic"
    else:
        ranking = EN_RANKING
        label = "EN/sub"

    for name in ranking:
        streams = await _try_provider(name, anilist_id, audio, ep, ctx)
        if streams:
            return streams

    logger.warning("[RACE] All %s providers failed (AniList=%s, episode=%s)", label, anilist_id, ep)
    return []


async def race_episodes(anilist_id: int, audio: str, ctx: dict) -> dict:
    """
    يجلب قائمة الحلقات من أول provider يرجع نتيجة
    """
    if audio == "ar":
        ranking = AR_RANKING
    else:
        ranking = EN_RANKING

    for name in ranking:
        mod = _load_provider(name)
        if mod is None:
            continue
        get_ep_fn = getattr(mod, "get_episodes", None)
        if get_ep_fn is None:
            continue
        try:
            result = await asyncio.wait_for(
                get_ep_fn(anilist_id, ctx),
                timeout=60.0,
            )
            if result:
                logger.warning("[RACE] episodes from '%s': %s keys", name, list(result.keys()))
                return result
        except asyncio.TimeoutError:
            logger.warning("[RACE] episodes timeout from '%s'", name)
        except Exception as e:
            logger.warning("[RACE] episodes error from '%s': %s", name, e)

    return {}
