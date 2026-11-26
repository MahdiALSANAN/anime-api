from __future__ import annotations
import asyncio
import importlib
import logging

logger = logging.getLogger(__name__)

AR_RANKING = ["gogoanime", "anime4up"]
EN_RANKING = ["gogoanime"]

WATCH_TIMEOUT = 75.0


def _load_provider(name: str):
    try:
        return importlib.import_module(f".{name}", package="src.providers")
    except ModuleNotFoundError:
        logger.warning("[RACE] provider '%s' not found", name)
        return None
    except Exception as e:
        logger.warning("[RACE] failed to load '%s': %s", name, e)
        return None


async def _try_provider(name: str, anilist_id: int, audio: str, ep: int, ctx: dict) -> list[dict]:
    mod = _load_provider(name)
    if mod is None:
        return []
    watch_fn = getattr(mod, "watch", None)
    if watch_fn is None:
        return []
    try:
        streams = await asyncio.wait_for(
            watch_fn(anilist_id, audio, ep, ctx),
            timeout=WATCH_TIMEOUT,
        )
        logger.warning("[RACE] %s → %d streams (id=%s ep=%s audio=%s)",
                       name, len(streams or []), anilist_id, ep, audio)
        return streams or []
    except asyncio.TimeoutError:
        logger.warning("[RACE] %s timeout (id=%s ep=%s)", name, anilist_id, ep)
        return []
    except Exception as e:
        logger.warning("[RACE] %s error: %s", name, e)
        return []


async def race_watch(
    anilist_id: int,
    ep: int | None = None,
    ctx: dict | None = None,
    lang: str = "sub",
    audio: str | None = None,
    preferred: str | None = None,
    **kwargs,
) -> list[dict]:
    ctx = ctx or {}
    effective = preferred or audio or lang or "sub"

    if effective == "ar":
        ranking = AR_RANKING
        label = "Arabic"
    else:
        ranking = EN_RANKING
        label = "sub"

    for name in ranking:
        streams = await _try_provider(name, anilist_id, effective, ep or 1, ctx)
        if streams:
            return streams

    logger.warning("[RACE] All %s providers failed (id=%s ep=%s)", label, anilist_id, ep)
    return []


async def race_episodes(
    anilist_id: int,
    ctx: dict | None = None,
    lang: str = "sub",
    audio: str | None = None,
    **kwargs,
) -> dict:
    ctx = ctx or {}
    effective = audio or lang or "sub"
    ranking = AR_RANKING if effective == "ar" else EN_RANKING

    for name in ranking:
        mod = _load_provider(name)
        if mod is None:
            continue
        fn = getattr(mod, "get_episodes", None)
        if fn is None:
            continue
        try:
            result = await asyncio.wait_for(fn(anilist_id, ctx), timeout=60.0)
            if result:
                logger.warning("[RACE] episodes from '%s'", name)
                return result
        except asyncio.TimeoutError:
            logger.warning("[RACE] episodes timeout '%s'", name)
        except Exception as e:
            logger.warning("[RACE] episodes error '%s': %s", name, e)

    return {}
