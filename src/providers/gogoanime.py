import httpx
import logging

log = logging.getLogger(__name__)

ANIWATCH_BASE = "https://hianime-api-production-6dfe2dde.up.railway.app"


async def watch(anilist_id: int, audio: str, ep: int, ctx: dict) -> list[dict]:
    titles = ctx.get("titles", [])
    if not titles:
        log.warning("gogoanime/aniwatch: no titles in ctx")
        return []

    category = "dub" if audio == "dub" else "sub"

    # نجرب كل عنوان حتى نلقى نتيجة
    for title in titles[:3]:
        slug = title.lower().replace(" ", "-")
        episode_id = f"{slug}-episode-{ep}"

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(
                    f"{ANIWATCH_BASE}/api/v2/hianime/episode/sources",
                    params={
                        "animeEpisodeId": episode_id,
                        "server": "hd-1",
                        "category": category,
                    },
                )
                log.info("aniwatch: %s → %s", episode_id, r.status_code)
                if r.status_code != 200:
                    continue

                data = r.json()
                sources = (data.get("data") or {}).get("sources", [])
                if not sources:
                    continue

                return [
                    {
                        "url": s["url"],
                        "quality": s.get("quality", "default"),
                        "provider": "aniwatch",
                    }
                    for s in sources
                ]
        except Exception as e:
            log.warning("aniwatch error (%s): %s", episode_id, e)
            continue

    return []
