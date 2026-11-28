from fastapi import APIRouter, Query, HTTPException, Path
from typing import Optional
import httpx
import base64
import re
from bs4 import BeautifulSoup

from src.queries import MEDIA_LIST_FIELDS, MEDIA_FULL_FIELDS
from src.parser import proxy_deep_images, inject_source_slugs, encode_pipe_request, decode_pipe_response
from src.extractor import anilist_query, fetch_raw_episodes
from src.config import iter_miruro_pipe_targets
from src.providers import _race as provider_race

router = APIRouter()


@router.get("/search")
async def search_anime(
    query: str,
    page: int = Query(1, ge=1, description="page number"),
    per_page: int = Query(20, ge=1, le=50, description="results per page"),
):

    gql = f"""
    query ($search: String, $page: Int, $perPage: Int) {{
        Page(page: $page, perPage: $perPage) {{
            pageInfo {{ total currentPage lastPage hasNextPage perPage }}
            media(search: $search, type: ANIME, sort: SEARCH_MATCH) {{
                {MEDIA_LIST_FIELDS}
            }}
        }}
    }}
    """
    data = await anilist_query(gql, {"search": query, "page": page, "perPage": per_page})
    page_data = data.get("Page", {})
    page_info = page_data.get("pageInfo", {})

    response = {
        "page": page_info.get("currentPage", page),
        "perPage": page_info.get("perPage", per_page),
        "total": page_info.get("total", 0),
        "hasNextPage": page_info.get("hasNextPage", False),
        "results": page_data.get("media", []),
    }
    return proxy_deep_images(response)


@router.get("/suggestions")
async def search_suggestions(
    query: str = Query(..., min_length=1, description="small search query for dropdowns"),
):

    gql = """
    query ($search: String) {
        Page(page: 1, perPage: 8) {
            media(search: $search, type: ANIME, sort: SEARCH_MATCH) {
                id
                title { romaji english }
                coverImage { large }
                format
                status
                startDate { year }
                episodes
            }
        }
    }
    """
    data = await anilist_query(gql, {"search": query})
    results = []

    for item in data.get("Page", {}).get("media", []):
        results.append({
            "id": item["id"],
            "title": item["title"].get("english") or item["title"].get("romaji"),
            "title_romaji": item["title"].get("romaji"),
            "poster": item["coverImage"]["large"],
            "format": item.get("format"),
            "status": item.get("status"),
            "year": (item.get("startDate") or {}).get("year"),
            "episodes": item.get("episodes"),
        })
    return proxy_deep_images({"suggestions": results})

SORT_MAP = {
    "SCORE_DESC": "SCORE_DESC",
    "POPULARITY_DESC": "POPULARITY_DESC",
    "TRENDING_DESC": "TRENDING_DESC",
    "START_DATE_DESC": "START_DATE_DESC",
    "FAVOURITES_DESC": "FAVOURITES_DESC",
    "UPDATED_AT_DESC": "UPDATED_AT_DESC",
}


@router.get("/filter")
async def filter_anime(
    genre: Optional[str] = Query(None, description="genre name"),
    tag: Optional[str] = Query(None, description="tag name"),
    year: Optional[int] = Query(None, description="season year"),
    season: Optional[str] = Query(None, description="season time"),
    format: Optional[str] = Query(None, description="media format"),
    status: Optional[str] = Query(None, description="release status"),
    sort: str = Query("POPULARITY_DESC", description="ordering logic"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=50),
):

    args = ["type: ANIME", f"sort: [{SORT_MAP.get(sort, 'POPULARITY_DESC')}]"]
    variables = {"page": page, "perPage": per_page}

    if genre:
        args.append("genre: $genre")
        variables["genre"] = genre
    if tag:
        args.append("tag: $tag")
        variables["tag"] = tag
    if year:
        args.append("seasonYear: $seasonYear")
        variables["seasonYear"] = year
    if season:
        args.append("season: $season")
        variables["season"] = season.upper()
    if format:
        args.append("format: $format")
        variables["format"] = format.upper()
    if status:
        args.append("status: $status")
        variables["status"] = status.upper()

    var_types = ["$page: Int", "$perPage: Int"]
    if genre:
        var_types.append("$genre: String")
    if tag:
        var_types.append("$tag: String")
    if year:
        var_types.append("$seasonYear: Int")
    if season:
        var_types.append("$season: MediaSeason")
    if format:
        var_types.append("$format: MediaFormat")
    if status:
        var_types.append("$status: MediaStatus")

    gql = f"""
    query ({', '.join(var_types)}) {{
        Page(page: $page, perPage: $perPage) {{
            pageInfo {{ total currentPage lastPage hasNextPage perPage }}
            media({', '.join(args)}) {{
                {MEDIA_LIST_FIELDS}
            }}
        }}
    }}
    """

    data = await anilist_query(gql, variables)
    page_data = data.get("Page", {})
    page_info = page_data.get("pageInfo", {})

    response = {
        "page": page_info.get("currentPage", page),
        "perPage": page_info.get("perPage", per_page),
        "total": page_info.get("total", 0),
        "hasNextPage": page_info.get("hasNextPage", False),
        "results": page_data.get("media", []),
    }
    return proxy_deep_images(response)


async def _fetch_collection(sort_type: str, status: str = None, page: int = 1, per_page: int = 20):
    status_filter = f", status: {status}" if status else ""
    gql = f"""
    query ($page: Int, $perPage: Int) {{
        Page(page: $page, perPage: $perPage) {{
            pageInfo {{ total currentPage lastPage hasNextPage perPage }}
            media(type: ANIME, sort: [{sort_type}]{status_filter}) {{
                {MEDIA_LIST_FIELDS}
            }}
        }}
    }}
    """
    data = await anilist_query(gql, {"page": page, "perPage": per_page})
    page_data = data.get("Page", {})
    page_info = page_data.get("pageInfo", {})

    response = {
        "page": page_info.get("currentPage", page),
        "perPage": page_info.get("perPage", per_page),
        "total": page_info.get("total", 0),
        "hasNextPage": page_info.get("hasNextPage", False),
        "results": page_data.get("media", []),
    }
    return proxy_deep_images(response)


@router.get("/spotlight")
async def get_spotlight():

    gql = f"""
    query {{
        Page(page: 1, perPage: 10) {{
            media(sort: [TRENDING_DESC, POPULARITY_DESC], type: ANIME) {{
                {MEDIA_LIST_FIELDS}
            }}
        }}
    }}
    """
    data = await anilist_query(gql)
    media = data.get("Page", {}).get("media", [])
    return proxy_deep_images({"results": media})


@router.get("/trending")
async def get_trending(page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=50)):

    return await _fetch_collection("TRENDING_DESC", page=page, per_page=per_page)


@router.get("/popular")
async def get_popular(page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=50)):

    return await _fetch_collection("POPULARITY_DESC", page=page, per_page=per_page)


@router.get("/upcoming")
async def get_upcoming(page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=50)):

    return await _fetch_collection("POPULARITY_DESC", "NOT_YET_RELEASED", page=page, per_page=per_page)


@router.get("/recent")
async def get_recent(page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=50)):

    return await _fetch_collection("START_DATE_DESC", "RELEASING", page=page, per_page=per_page)


@router.get("/schedule")
async def get_schedule(page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=50)):

    gql = f"""
    query ($page: Int, $perPage: Int) {{
        Page(page: $page, perPage: $perPage) {{
            pageInfo {{ total currentPage lastPage hasNextPage perPage }}
            airingSchedules(notYetAired: true, sort: TIME) {{
                episode
                airingAt
                timeUntilAiring
                media {{
                    {MEDIA_LIST_FIELDS}
                }}
            }}
        }}
    }}
    """
    data = await anilist_query(gql, {"page": page, "perPage": per_page})
    page_data = data.get("Page", {})
    page_info = page_data.get("pageInfo", {})

    results = []
    for item in page_data.get("airingSchedules", []):
        entry = item.get("media", {})
        entry["next_episode"] = item.get("episode")
        entry["airingAt"] = item.get("airingAt")
        entry["timeUntilAiring"] = item.get("timeUntilAiring")
        results.append(entry)

    response = {
        "page": page_info.get("currentPage", page),
        "perPage": page_info.get("perPage", per_page),
        "total": page_info.get("total", 0),
        "hasNextPage": page_info.get("hasNextPage", False),
        "results": results,
    }
    return proxy_deep_images(response)


@router.get("/info/{anilist_id}")
async def get_anime_info(anilist_id: int):

    gql = f"""
    query ($id: Int) {{
        Media(id: $id, type: ANIME) {{
            {MEDIA_FULL_FIELDS}
        }}
    }}
    """
    data = await anilist_query(gql, {"id": anilist_id})
    media = data.get("Media")
    if not media:
        raise HTTPException(status_code=404, detail="anime not found sorry")
    return proxy_deep_images(media)


@router.get("/anime/{anilist_id}/characters")
async def get_anime_characters(
    anilist_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=50),
):

    gql = """
    query ($id: Int, $page: Int, $perPage: Int) {
        Media(id: $id, type: ANIME) {
            id
            title { romaji english }
            characters(sort: [ROLE, RELEVANCE], page: $page, perPage: $perPage) {
                pageInfo { total currentPage lastPage hasNextPage perPage }
                edges {
                    role
                    node {
                        id
                        name { full native userPreferred }
                        image { large medium }
                        description
                        gender
                        dateOfBirth { year month day }
                        age
                        favourites
                        siteUrl
                    }
                    voiceActors {
                        id
                        name { full native }
                        image { large }
                        languageV2
                    }
                }
            }
        }
    }
    """
    data = await anilist_query(gql, {"id": anilist_id, "page": page, "perPage": per_page})
    media = data.get("Media")
    if not media:
        raise HTTPException(status_code=404, detail="couldnt locate anime")

    chars = media.get("characters", {})
    page_info = chars.get("pageInfo", {})

    response = {
        "page": page_info.get("currentPage", page),
        "perPage": page_info.get("perPage", per_page),
        "total": page_info.get("total", 0),
        "hasNextPage": page_info.get("hasNextPage", False),
        "characters": chars.get("edges", []),
    }
    return proxy_deep_images(response)


@router.get("/anime/{anilist_id}/relations")
async def get_anime_relations(anilist_id: int):

    gql = """
    query ($id: Int) {
        Media(id: $id, type: ANIME) {
            id
            title { romaji english }
            relations {
                edges {
                    relationType(version: 2)
                    node {
                        id
                        title { romaji english native }
                        coverImage { large }
                        bannerImage
                        format
                        type
                        status
                        episodes
                        chapters
                        meanScore
                        averageScore
                        popularity
                        startDate { year month day }
                    }
                }
            }
        }
    }
    """
    data = await anilist_query(gql, {"id": anilist_id})
    media = data.get("Media")
    if not media:
        raise HTTPException(status_code=404, detail="not found")

    response = {
        "id": media["id"],
        "title": media["title"],
        "relations": media.get("relations", {}).get("edges", []),
    }
    return proxy_deep_images(response)


@router.get("/anime/{anilist_id}/recommendations")
async def get_anime_recommendations(
    anilist_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=25),
):

    gql = """
    query ($id: Int, $page: Int, $perPage: Int) {
        Media(id: $id, type: ANIME) {
            id
            title { romaji english }
            recommendations(sort: RATING_DESC, page: $page, perPage: $perPage) {
                pageInfo { total currentPage lastPage hasNextPage perPage }
                nodes {
                    rating
                    mediaRecommendation {
                        id
                        title { romaji english native }
                        coverImage { large extraLarge }
                        bannerImage
                        format
                        episodes
                        status
                        meanScore
                        averageScore
                        popularity
                        genres
                        startDate { year }
                    }
                }
            }
        }
    }
    """
    data = await anilist_query(gql, {"id": anilist_id, "page": page, "perPage": per_page})
    media = data.get("Media")
    if not media:
        raise HTTPException(status_code=404, detail="where the anime at")

    recs = media.get("recommendations", {})
    page_info = recs.get("pageInfo", {})

    response = {
        "page": page_info.get("currentPage", page),
        "perPage": page_info.get("perPage", per_page),
        "total": page_info.get("total", 0),
        "hasNextPage": page_info.get("hasNextPage", False),
        "recommendations": recs.get("nodes", []),
    }
    return proxy_deep_images(response)


@router.get("/episodes/{anilist_id}")
async def get_episodes(anilist_id: int):

    try:
        native = await provider_race.merged_episodes(anilist_id)
        if native:
            return proxy_deep_images(native)
    except Exception as e:
        print(f"[KUHI API] native episodes failed, falling back to pipe: {e}")
    data = await fetch_raw_episodes(anilist_id)
    return proxy_deep_images(inject_source_slugs(data, anilist_id))


@router.get("/providers/status")
async def get_providers_status():

    return provider_race.providers_status()


@router.get("/bridge-status")
async def get_bridge_status():

    return provider_race.providers_status()


@router.get("/genres")
async def get_genres():

    gql = """
    query {
        GenreCollection
    }
    """
    data = await anilist_query(gql)
    return {"genres": data.get("GenreCollection", [])}


@router.get("/sources")
async def get_sources(
    episodeId: str = Query(..., description="episode tracking string"),
    provider: str = Query(..., description="video provider host"),
    anilistId: int = Query(..., description="anime db identification code"),
    category: str = Query("sub", description="dubbed or subbed version"),
):

    enc_id = base64.urlsafe_b64encode(episodeId.encode()).decode().rstrip('=')
    payload = {
        "path": "sources",
        "method": "GET",
        "query": {
            "episodeId": enc_id,
            "provider": provider,
            "category": category,
            "anilistId": anilistId,
        },
        "body": None,
        "version": "0.1.0",
    }
    encoded_req = encode_pipe_request(payload)

    async with httpx.AsyncClient(timeout=15.0) as client:
        last_status = None
        for pipe_target, headers in iter_miruro_pipe_targets(encoded_req):
            print(f"\n[KUHI API] extracting stream sources pipe targeting:", pipe_target)
            try:
                res = await client.get(pipe_target, headers=headers)
                last_status = res.status_code
                if res.status_code == 200:
                    return proxy_deep_images(decode_pipe_response(res.text.strip()))
            except Exception as e:
                print(f"[KUHI API] Failed to connect to {pipe_target}: {e}")

        raise HTTPException(status_code=last_status or 502, detail="fetching pipe failed real bad")


@router.get("/extract/{query}")
async def extract_simple(
    query: str,
    episode: int = Query(1, alias="e"),
    audio: str = Query("sub", alias="type", description="sub or dub"),
    provider: str | None = Query(None, alias="provider", description="prefer one provider"),
    lang: str = Query("en", alias="lang", description="language: en or ar"),
):

    audio = (audio or "sub").lower()
    if audio not in ("sub", "dub"):
        raise HTTPException(status_code=422, detail="type must be sub or dub")
    lang = (lang or "en").lower()
    wanted = (provider or "").lower() or None
    all_providers = provider_race.RANKING + provider_race.AR_RANKING
    if wanted and wanted not in all_providers:
        raise HTTPException(status_code=422, detail=f"unknown provider '{provider}'. valid: {', '.join(all_providers)}")

    if query.isdigit():
        anilist_id = int(query)

        gql = """
        query ($id: Int) {
            Media(id: $id, type: ANIME) {
                id
                format
            }
        }
        """
        media_data = await anilist_query(gql, {"id": anilist_id})
        media = media_data.get("Media")
        if not media:
            raise HTTPException(status_code=404, detail="anime not found")

        if media.get("format") == "MOVIE":
            episode = 1
    else:

        gql = f"""
        query ($search: String) {{
            Page(page: 1, perPage: 1) {{
                media(search: $search, type: ANIME, sort: SEARCH_MATCH) {{
                    id
                    format
                }}
            }}
        }}
        """
        search_data = await anilist_query(gql, {"search": query.replace("-", " ")})
        media_list = search_data.get("Page", {}).get("media", [])
        if not media_list:
            raise HTTPException(status_code=404, detail="anime not found")

        anilist_id = media_list[0]["id"]
        media_format = media_list[0].get("format")

        if media_format == "MOVIE":
            episode = 1

    try:
        raced = await provider_race.race_watch(anilist_id, episode, audio, preferred=wanted, lang=lang)
        if raced:
            result = {
                "anilistId": anilist_id,
                "episode": episode,
                "type": audio,
                "lang": raced.get("lang", lang),
                "provider": raced["provider"],
                "defaultProvider": raced.get("defaultProvider"),
                "requestedProvider": raced.get("requestedProvider"),
                "streams": raced["streams"],
                "subtitles": provider_race.merge_subtitles(raced["streams"]),
            }
            return proxy_deep_images(result)
    except Exception as e:
        print(f"[KUHI API] native race failed, falling back to pipe: {e}")

    data = await fetch_raw_episodes(anilist_id)
    providers = data.get("providers", {})

    if not providers:
        raise HTTPException(status_code=404, detail="no providers available for this anime")

    target_episode_id = None
    target_provider = None
    target_cat = None

    ranking = ["zoro", "bee", "telli", "arc", "yugen", "jet", "neo", "kiwi"]

    for prov in ranking:
        if prov in providers:
            prov_data = providers[prov]
            eps = prov_data.get("episodes", {})
            for cat in ["sub", "dub", "raw"]:
                if cat in eps:
                    cat_eps = eps[cat]
                    if isinstance(cat_eps, list):
                        for ep in cat_eps:
                            if ep.get("number") == episode:
                                target_episode_id = ep.get("id")
                                target_provider = prov
                                target_cat = cat
                                break
                if target_episode_id:
                    break
        if target_episode_id:
            break

    if not target_episode_id:
        raise HTTPException(status_code=404, detail=f"episode {episode} not found in any provider")

    last_error = None
    providers_to_try = [(target_provider, target_cat, target_episode_id)]

    for prov in ranking:
        if prov != target_provider and prov in providers:
            prov_data = providers[prov]
            eps = prov_data.get("episodes", {})
            for cat in ["sub", "dub", "raw"]:
                if cat in eps:
                    cat_eps = eps[cat]
                    if isinstance(cat_eps, list):
                        for ep in cat_eps:
                            if ep.get("number") == episode:
                                providers_to_try.append((prov, cat, ep.get("id")))
                                break

    for try_provider, try_cat, try_ep_id in providers_to_try:
        try:
            enc_id = base64.urlsafe_b64encode(try_ep_id.encode()).decode().rstrip('=')
            payload = {
                "path": "sources",
                "method": "GET",
                "query": {
                    "episodeId": enc_id,
                    "provider": try_provider,
                    "category": try_cat,
                    "anilistId": anilist_id,
                },
                "body": None,
                "version": "0.1.0",
            }
            encoded_req = encode_pipe_request(payload)

            async with httpx.AsyncClient(timeout=15.0) as client:
                for pipe_target, headers in iter_miruro_pipe_targets(encoded_req):
                    print(f"\n[KUHI API] trying provider {try_provider}: {pipe_target}")
                    try:
                        res = await client.get(pipe_target, headers=headers)

                        if res.status_code == 200:
                            result = decode_pipe_response(res.text.strip())
                            print(f"[KUHI API] success with provider {try_provider}")
                            return proxy_deep_images(result)

                        last_error = f"{try_provider} failed: {res.status_code}"
                        print(f"[KUHI API] {last_error}")
                    except Exception as e:
                        print(f"[KUHI API] Failed to connect to {pipe_target}: {e}")
        except Exception as e:
            last_error = f"{try_provider} error: {str(e)}"
            print(f"[KUHI API] {last_error}")
            continue

    if audio == "dub":
        raise HTTPException(
            status_code=404,
            detail=f"no dub streams found for episode {episode}; try type=sub",
        )
    raise HTTPException(status_code=500, detail=f"all providers failed. last error: {last_error}")


# ---------------------------------------------------------------------------
# sanime.net Arabic provider — direct MP4 stream by AniList ID
# ---------------------------------------------------------------------------

@router.get("/sanime/{anilist_id}")
async def get_sanime_stream(
    anilist_id: int,
    episode: int = Query(1, alias="e", description="episode number (1-based)"),
):
    """
    Returns the direct MP4 URL for an episode from sanime.net.
    Mapping: AniList ID -> sanime internal ID + subdirectory.
    URL pattern: https://server.sanime.net/{dir}/{id}/{episode}.mp4
    """
    # AniList ID -> {"id": <sanime_id>, "dir": <subdirectory>}
    # URL pattern: https://server.sanime.net/{dir}/{id}/{episode}.mp4
    # Directories confirmed from OtakuTime/sanime app: Video, Video2, Video3
    # Note: server.sanime.net returns 200 on HEAD for any path — probing is unreliable.
    # Use the confirmed dir from OtakuTime traffic capture.
    SANIME_MAP: dict[int, dict] = {
        20:     {"id": 512,   "dir": "Video2"},   # Naruto (2002)
        21:     {"id": 532,   "dir": "Video2"},   # One Piece — confirmed Video2 from OtakuTime ep1189
        11061:  {"id": 669,   "dir": "Video3"},   # Hunter x Hunter (2011)
        235:    {"id": 653,   "dir": "Video"},    # Detective Conan / Case Closed
        142592: {"id": 11989, "dir": "Video"},    # Bleach: TYBW
        97940:  {"id": 12012, "dir": "Video"},    # Black Clover S2 (2026)
        100146: {"id": 11950, "dir": "Video"},    # Black Torch (2026)
        # Add more entries here as new anime are confirmed
    }

    entry = SANIME_MAP.get(anilist_id)
    if not entry:
        raise HTTPException(
            status_code=404,
            detail=f"no sanime mapping for anilist_id={anilist_id}. "
                   f"Known IDs: {sorted(SANIME_MAP.keys())}",
        )

    url = f"https://server.sanime.net/{entry['dir']}/{entry['id']}/{episode}.mp4"
    return {
        "anilist_id":   anilist_id,
        "sanime_id":    entry["id"],
        "subdirectory": entry["dir"],
        "episode":      episode,
        "url":          url,
        "headers": {
            "Referer": "https://sanime.net/",
        },
    }


# ---------------------------------------------------------------------------
# sanime.net — scrape anime list page to discover ID mappings
# ---------------------------------------------------------------------------

@router.get("/sanime-discover")
async def sanime_discover(
    page: int = Query(1, ge=1, le=50, description="page number on sanime.net anime list"),
):
    """
    Fetches sanime.net anime list page and extracts anime slugs/IDs.
    Use this to discover sanime internal IDs so you can manually confirm
    which Video/Video2/Video3 directory each anime uses.

    Returns raw extracted data — IDs still need directory confirmation via OtakuTime.
    """
    import re

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ar,en;q=0.5",
        "Referer": "https://sanime.net/",
    }

    url = f"https://sanime.net/anime-list/?page={page}"

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"sanime.net returned {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to reach sanime.net: {str(e)}")

    html = resp.text

    # Extract anime entries: href="/anime/<slug>/<id>/" pattern
    # sanime URLs look like: /anime/one-piece/532/ or /watch/532/1/
    anime_links = re.findall(r'href=["\'](?:https://sanime\.net)?/anime/([^/"]+)/(\d+)/?["\']', html)
    watch_links = re.findall(r'href=["\'](?:https://sanime\.net)?/watch/(\d+)/\d+/?["\']', html)

    # Also try to extract from data attributes or JSON in page
    json_ids = re.findall(r'"anime_id"\s*:\s*"?(\d+)"?', html)
    slug_id_pairs = re.findall(r'/anime/([a-z0-9\-]+)/(\d+)', html)

    # Deduplicate
    seen_ids = set()
    results = []
    for slug, sid in slug_id_pairs:
        if sid not in seen_ids:
            seen_ids.add(sid)
            results.append({"slug": slug, "sanime_id": int(sid)})

    # Also add IDs found in watch links
    for sid in watch_links:
        if sid not in seen_ids:
            seen_ids.add(sid)
            results.append({"slug": None, "sanime_id": int(sid)})

    return {
        "page": page,
        "source_url": url,
        "status_code": resp.status_code,
        "anime_count": len(results),
        "anime": results,
        "note": "Directory (Video/Video2/Video3) still needs confirmation via OtakuTime for each entry.",
        "html_length": len(html),
        "html_preview": html[:500] if html else "",
    }


@router.get("/sanime-search")
async def sanime_search(
    q: str = Query(..., description="Anime title to search on sanime.net (Arabic or English)"),
):
    """
    Search sanime.net for an anime by title.
    Returns sanime internal ID and slug which you can use to build the MP4 URL
    (after confirming the directory via OtakuTime).
    """
    import re

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ar,en;q=0.5",
        "Referer": "https://sanime.net/",
    }

    # Try sanime search endpoint
    search_url = f"https://sanime.net/?s={q}"

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(search_url, headers=headers)
            resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"sanime.net returned {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to reach sanime.net: {str(e)}")

    html = resp.text

    slug_id_pairs = re.findall(r'/anime/([a-z0-9\-]+)/(\d+)', html)
    seen_ids = set()
    results = []
    for slug, sid in slug_id_pairs:
        if sid not in seen_ids:
            seen_ids.add(sid)
            results.append({"slug": slug, "sanime_id": int(sid)})

    return {
        "query": q,
        "search_url": search_url,
        "status_code": resp.status_code,
        "results_count": len(results),
        "results": results,
        "html_length": len(html),
        "html_preview": html[:300] if html else "",
        "note": "Confirm directory (Video/Video2/Video3) via OtakuTime before adding to SANIME_MAP.",
    }


# ---------------------------------------------------------------------------
# Anime4Up — stream extractor  (rewritten 2026-11-29)
# ---------------------------------------------------------------------------
# Entry:  https://www.anime4up.bond/  →  302  →  https://4i.nku67qv.shop
# Episode URL pattern:
#   https://4i.nku67qv.shop/episode/انمي-{slug}-الحلقة-{n}-مترجمة/
# AES key/IV: b"4^TT%zCMd4L4ma4Z" (CBC, PKCS7)
# Server list: <ul class="server-list"> or <ul class="anime_muti_link">
#              <li data-video="BASE64_AES_CIPHERTEXT">
# ---------------------------------------------------------------------------

_A4U_BOND = "https://www.anime4up.bond/"
_A4U_LIVE = "https://4i.nku67qv.shop"          # confirmed live domain
_A4U_KEY  = b"4^TT%zCMd4L4ma4Z"                # AES-128-CBC key AND iv

_A4U_UA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": _A4U_LIVE + "/",
}


def _a4u_decrypt(cipher_b64: str) -> str:
    """AES-128-CBC decrypt a base64-encoded ciphertext; key == iv == _A4U_KEY."""
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    data = base64.b64decode(cipher_b64 + "==")   # pad just in case
    cipher = AES.new(_A4U_KEY, AES.MODE_CBC, iv=_A4U_KEY)
    plain = unpad(cipher.decrypt(data), AES.block_size)
    return plain.decode("utf-8")


def _a4u_parse_servers(html: str) -> list[dict]:
    """
    Extract server list from an anime4up episode page.
    Returns list of {label, url, encrypted} dicts.
    """
    soup = BeautifulSoup(html, "html.parser")
    servers: list[dict] = []
    seen: set[str] = set()

    def add(label: str, raw: str) -> None:
        raw = raw.strip()
        if not raw or raw in seen:
            return
        seen.add(raw)
        # Try AES decrypt; if it fails keep raw value
        url = raw
        encrypted = False
        try:
            decrypted = _a4u_decrypt(raw)
            if decrypted.startswith("http"):
                url = decrypted
                encrypted = True
        except Exception:
            pass
        servers.append({"label": label, "url": url, "encrypted": encrypted})

    # Selector A — <ul class="server-list"> / <ul class="anime_muti_link">
    for li in soup.select("ul.server-list li, ul.anime_muti_link li"):
        raw = (
            li.get("data-video")
            or li.get("data-src")
            or li.get("data-ep-url")
            or ""
        )
        label = li.get_text(strip=True) or li.get("data-name") or "server"
        if raw:
            add(label, raw)

    # Selector B — <div id="episode-servers"> classic
    for a in soup.select("#episode-servers li a[data-ep-url]"):
        raw = a.get("data-ep-url", "")
        label = a.get_text(strip=True) or "server"
        if raw:
            add(label, raw)

    # Selector C — video/source tags (direct streams, unencrypted)
    for tag in soup.select("video[src], video source[src]"):
        src = tag.get("src", "")
        if src.startswith("http") and (".mp4" in src or ".m3u8" in src):
            add("direct", src)

    # Selector D — iframes
    for iframe in soup.select("iframe[src]"):
        src = iframe.get("src", "")
        if src.startswith("http"):
            add("iframe", src)

    return servers


@router.get("/anime4up/{slug}")
async def get_anime4up_stream(
    slug: str = Path(..., description="Anime slug, e.g. black-clover-2nd-season"),
    episode: int = Query(1, alias="e", ge=1, description="Episode number (1-based)"),
):
    """
    Return streaming server URLs for an anime4up episode.

    URL format: /anime/anime4up/{slug}?e={episode_number}
    Example:    /anime/anime4up/black-clover-2nd-season?e=9
    """
    # Always use the confirmed live domain directly — bond redirect is blocked on Render
    async with httpx.AsyncClient(
        headers=_A4U_UA_HEADERS,
        follow_redirects=True,
        timeout=20,
    ) as client:

        live_base = _A4U_LIVE  # hardcoded: 4i.nku67qv.shop

        # Step 2 — build episode URL (Arabic pattern)
        ep_url_arabic = (
            f"{live_base}/episode/"
            f"انمي-{slug}"
            f"-الحلقة-{episode}"
            f"-مترجمة/"
        )
        # Fallback patterns
        ep_urls = [
            ep_url_arabic,
            f"{live_base}/episode/{slug}-{episode}/",
            f"{live_base}/watch/{slug}-episode-{episode}/",
        ]

        html = ""
        final_url = ""
        tried: list[str] = []

        for ep_url in ep_urls:
            tried.append(ep_url)
            try:
                resp = await client.get(ep_url, timeout=20)
                if resp.status_code == 200 and len(resp.text) > 500:
                    # Quick sanity — page should mention the slug or Arabic episode text
                    if slug in resp.text or "حلقة" in resp.text:
                        html = resp.text
                        final_url = str(resp.url)
                        break
                    # Accept anyway if HTML is substantial
                    if len(resp.text) > 5000:
                        html = resp.text
                        final_url = str(resp.url)
                        break
            except Exception:
                continue

        if not html:
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "Could not fetch anime4up episode page",
                    "tried": tried,
                    "live_base": live_base,
                    "hint": "Slug must be the English anime4up URL slug, e.g. black-clover-2nd-season",
                },
            )

        # Step 3 — extract servers
        servers = _a4u_parse_servers(html)

        # Step 4 — pick best stream URL for AVPlayer
        #   prefer m3u8 > mp4 > iframe/embed
        stream_url: str | None = None
        for s in servers:
            u = s["url"]
            if u.startswith("http") and (".m3u8" in u or ".mp4" in u):
                stream_url = u
                break
        if not stream_url and servers:
            # Return first URL even if embed
            stream_url = servers[0]["url"] if servers[0]["url"].startswith("http") else None

        referer = live_base + "/"

        return {
            "slug": slug,
            "episode": episode,
            "live_base": live_base,
            "episode_page": final_url,
            "servers": servers,
            "servers_count": len(servers),
            "stream_url": stream_url,
            "headers": {
                "Referer": referer,
                "Origin": live_base,
            },
            "debug": {
                "tried_urls": tried,
                "html_length": len(html),
                "html_snippet": html[:600],
            },
        }
