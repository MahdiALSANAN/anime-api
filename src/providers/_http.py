import logging
from urllib.parse import quote

import httpx
from curl_cffi.requests import AsyncSession

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

HTML_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
}

JSON_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
}

def _response_error(res) -> RuntimeError:
    return RuntimeError(f"HTTP {res.status_code} fetching {res.url} | server={res.headers.get('server')}")

async def fetch_text(url: str, headers: dict | None = None, timeout: float = 20.0) -> str:
    request_headers = dict(HTML_HEADERS)
    if headers:
        request_headers.update(headers)

    # إذا كان الطلب لموقع witanime، نمرره عبر بروكسي لتخطي حظر Cloudflare
    if "witanime.site" in url:
        proxies = [
            f"https://api.allorigins.win/raw?url={quote(url, safe='')}",
            f"https://api.codetabs.com/v1/proxy?quest={quote(url, safe='')}",
            f"https://corsproxy.io/?{quote(url, safe='')}"
        ]
        async with httpx.AsyncClient() as client:
            for proxy_url in proxies:
                try:
                    res = await client.get(proxy_url, timeout=15.0)
                    if res.status_code == 200 and "Just a moment" not in res.text:
                        return res.text
                except Exception:
                    continue

    # الطريقة العادية (لمواقع أخرى)
    async with AsyncSession() as session:
        try:
            res = await session.get(
                url, headers=request_headers, timeout=timeout,
                impersonate="chrome124", allow_redirects=True
            )
        except Exception as exc:
            raise RuntimeError(f"Request failed for {url}: {exc}")

    if res.status_code != 200:
        raise _response_error(res)

    return res.text

async def fetch_json(url: str, headers: dict | None = None, timeout: float = 20.0):
    request_headers = dict(JSON_HEADERS)
    if headers:
        request_headers.update(headers)

    async with AsyncSession() as session:
        res = await session.get(url, headers=request_headers, timeout=timeout, impersonate="chrome124", allow_redirects=True)
    
    if res.status_code != 200:
        raise _response_error(res)
    return res.json()
