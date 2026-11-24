
# ---------------------------------------------------------
# Search — multiple discovery methods
# ---------------------------------------------------------

_SITEMAP_CACHE = None


def _normalize_title(value: str) -> str:
    value = html.unescape(decode_entities(value or ""))
    value = strip_tags(value).lower()
    value = re.sub(r"[\-_]+", " ", value)
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _extract_anime_results(source: str) -> list:
    results = []
    seen = set()

    # Method 1: normal HTML anchors
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

        title = strip_tags(title).strip() or slug.replace("-", " ")

        seen.add(slug.lower())
        results.append({
            "slug": slug,
            "text": title,
        })

    # Method 2: extract anime URLs even if HTML nesting is unusual
    patterns = [
        r"""href\s*=\s*["']([^"']*/anime/[^"'?#]+)""",
        r"""["'](https?://witanime\.site/anime/[^"'?#]+)""",
        r"""["'](/anime/[^"'?#]+)""",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, source, re.IGNORECASE):
            href = html.unescape(match.group(1))
            slug = _slug_from_url(href, "anime")

            if not slug or slug.lower() in seen:
                continue

            seen.add(slug.lower())
            results.append({
                "slug": slug,
                "text": slug.replace("-", " "),
            })

    return results


async def _get_sitemap_results(query: str) -> list:
    global _SITEMAP_CACHE

    if _SITEMAP_CACHE is None:
        _SITEMAP_CACHE = []

        sitemap_urls = [
            f"{BASE}/wp-sitemap-posts-anime-1.xml",
            f"{BASE}/anime-sitemap.xml",
            f"{BASE}/sitemap_index.xml",
            f"{BASE}/wp-sitemap.xml",
        ]

        headers = {
            "Referer": BASE + "/",
            "Accept": "application/xml,text/xml,text/html,*/*",
            "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        }

        discovered_sitemaps = []

        for sitemap_url in sitemap_urls:
            try:
                body = await fetch_text(sitemap_url, headers)

                if not body or len(body) < 30:
                    continue

                locations = re.findall(
                    r"<loc>\s*(.*?)\s*</loc>",
                    body,
                    re.IGNORECASE | re.DOTALL,
                )

                if locations:
                    for location in locations:
                        location = html.unescape(location.strip())

                        if "anime" in location.lower():
                            discovered_sitemaps.append(location)

                    if "wp-sitemap-posts-anime-1.xml" in sitemap_url:
                        discovered_sitemaps.append(sitemap_url)

            except Exception as e:
                print(
                    f"[WITANIME] Sitemap request failed "
                    f"({sitemap_url}): {type(e).__name__}: {e}",
                    flush=True,
                )

        # Include direct sitemap URLs and discovered anime sitemaps.
        for sitemap_url in dict.fromkeys(discovered_sitemaps):
            try:
                body = await fetch_text(sitemap_url, headers)

                locations = re.findall(
                    r"<loc>\s*(.*?)\s*</loc>",
                    body or "",
                    re.IGNORECASE | re.DOTALL,
                )

                for location in locations:
                    location = html.unescape(location.strip())
                    slug = _slug_from_url(location, "anime")

                    if slug:
                        _SITEMAP_CACHE.append({
                            "slug": slug,
                            "text": slug.replace("-", " "),
                        })

            except Exception as e:
                print(
                    f"[WITANIME] Anime sitemap failed: "
                    f"{type(e).__name__}: {e}",
                    flush=True,
                )

        # Remove duplicate slugs.
        unique = {}
        for item in _SITEMAP_CACHE:
            unique[item["slug"].lower()] = item

        _SITEMAP_CACHE = list(unique.values())

        print(
            f"[WITANIME] Sitemap anime entries: "
            f"{len(_SITEMAP_CACHE)}",
            flush=True,
        )

    query_norm = _normalize_title(query)

    if not query_norm:
        return []

    query_words = set(query_norm.split())
    ranked = []

    for item in _SITEMAP_CACHE:
        title_norm = _normalize_title(
            item["text"] + " " + item["slug"].replace("-", " ")
        )

        title_words = set(title_norm.split())

        similarity = difflib.SequenceMatcher(
            None,
            query_norm,
            title_norm,
        ).ratio()

        if query_words and title_words:
            overlap = len(query_words & title_words) / len(query_words)
        else:
            overlap = 0

        score = max(similarity, overlap)

        if score >= 0.35 or query_norm in title_norm:
            ranked.append((score, item))

    ranked.sort(key=lambda item: item[0], reverse=True)

    return [item for _, item in ranked[:30]]


async def _search_wordpress_api(query: str) -> list:
    results = []

    endpoints = [
        f"{BASE}/wp-json/wp/v2/anime?search={quote(query)}&per_page=30",
        f"{BASE}/wp-json/wp/v2/animes?search={quote(query)}&per_page=30",
    ]

    headers = {
        "Referer": BASE + "/",
        "Accept": "application/json",
        "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
    }

    for url in endpoints:
        try:
            body = await fetch_text(url, headers)

            import json
            data = json.loads(body)

            if not isinstance(data, list):
                continue

            for item in data:
                link = item.get("link", "")
                slug = _slug_from_url(link, "anime")

                if not slug:
                    continue

                title_data = item.get("title", {})
                title = (
                    title_data.get("rendered", "")
                    if isinstance(title_data, dict)
                    else str(title_data)
                )

                results.append({
                    "slug": slug,
                    "text": strip_tags(title).strip()
                    or slug.replace("-", " "),
                })

        except Exception as e:
            print(
                f"[WITANIME] WordPress API unavailable: "
                f"{type(e).__name__}: {e}",
                flush=True,
            )

    return results


async def search(query: str) -> list:
    print(f"[WITANIME] Searching: {query}", flush=True)

    headers = {
        "Referer": BASE + "/",
        "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
    }

    results = []
    seen = set()

    def add_items(items):
        for item in items:
            slug = item.get("slug", "").strip()

            if not slug or slug.lower() in seen:
                continue

            seen.add(slug.lower())
            results.append(item)

    # 1. Website search page
    search_url = (
        f"{BASE}/?search_param=animes"
        f"&s={quote(query)}"
    )

    try:
        body = await fetch_text(search_url, headers)

        print(
            f"[WITANIME] Search page received: "
            f"{len(body or '')} characters",
            flush=True,
        )

        add_items(_extract_anime_results(body or ""))

    except Exception as e:
        print(
            f"[WITANIME] Website search failed: "
            f"{type(e).__name__}: {e}",
            flush=True,
        )

    # 2. WordPress REST API
    if not results:
        add_items(await _search_wordpress_api(query))

    # 3. Anime sitemap fallback
    if not results:
        add_items(await _get_sitemap_results(query))

    print(
        f"[WITANIME] Search returned {len(results)} results "
        f"for {query}",
        flush=True,
    )

    for item in results[:10]:
        print(
            f"[WITANIME] Candidate: "
            f"{item['text']} -> {item['slug']}",
            flush=True,
        )

    return results


async def search_fn(query: str) -> list:
    return await search(query)
