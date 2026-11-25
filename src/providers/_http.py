import logging
from curl_cffi.requests import AsyncSession

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

HTML_HEADERS = {
    "User-Agent": UA,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}

JSON_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
}

def _response_error(res) -> RuntimeError:
    headers = res.headers
    server = headers.get("server", "unknown")
    cf_ray = headers.get("cf-ray", "none")
    content_type = headers.get("content-type", "unknown")

    try:
        body = res.text[:500].replace("\n", " ").replace("\r", " ")
    except Exception:
        body = "<unable to decode response body>"

    message = (
        f"HTTP {res.status_code} fetching {res.url} | "
        f"server={server} | "
        f"cf-ray={cf_ray} | "
        f"content-type={content_type} | "
        f"body={body}"
    )
    logger.warning("[HTTP] %s", message)
    return RuntimeError(message)

async def fetch_text(
    url: str,
    headers: dict | None = None,
    timeout: float = 20.0,
) -> str:
    request_headers = dict(HTML_HEADERS)
    if headers:
        request_headers.update(headers)

    # استخدام curl_cffi مع محاكاة متصفح كروم لتجاوز حماية Cloudflare
    async with AsyncSession() as session:
        try:
            res = await session.get(
                url,
                headers=request_headers,
                timeout=timeout,
                impersonate="chrome124",
                allow_redirects=True
            )
        except Exception as exc:
            logger.warning("[HTTP] Request error for %s: %s", url, exc)
            raise RuntimeError(
                f"Request failed for {url}: {type(exc).__name__}: {exc}"
            ) from exc

    if res.status_code != 200:
        raise _response_error(res)

    return res.text

async def fetch_json(
    url: str,
    headers: dict | None = None,
    timeout: float = 20.0,
):
    request_headers = dict(JSON_HEADERS)
    if headers:
        request_headers.update(headers)

    async with AsyncSession() as session:
        try:
            res = await session.get(
                url,
                headers=request_headers,
                timeout=timeout,
                impersonate="chrome124",
                allow_redirects=True
            )
        except Exception as exc:
            logger.warning("[HTTP] Request error for %s: %s", url, exc)
            raise RuntimeError(
                f"Request failed for {url}: {type(exc).__name__}: {exc}"
            ) from exc

    if res.status_code != 200:
        raise _response_error(res)

    try:
        return res.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid JSON response from {res.url}: "
            f"{res.text[:300]}"
        ) from exc
