import logging
from urllib.parse import quote

import httpx
from curl_cffi.requests import AsyncSession

logger = logging.getLogger(__name__)

# ⚠️ ضع رابط الـ Cloudflare Worker الخاص بك هنا بين علامات التنصيص
# مثال: WORKER_URL = "https://witanime-proxy.username.workers.dev"
WORKER_URL = "https://witanime-proxy.97pbrzpbvr.workers.dev/" 

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

    # إذا كان الطلب لموقع witanime، نمرره عبر الـ Worker الخاص بك
    if "witanime.site" in url:
        if "ضع_رابط_الوركر_هنا" in WORKER_URL:
            print("[PROXY ERROR] يرجى وضع رابط الـ Worker في ملف _http.py", flush=True)
        else:
            proxy_url = f"{WORKER_URL}?url={quote(url, safe='')}"
            async with httpx.AsyncClient() as client:
                try:
                    # نرسل الطلب للوسيط الخاص بك
                    res = await client.get(proxy_url, timeout=30.0)
                    if res.status_code == 200:
                        return res.text
                    else:
                        print(f"[PROXY ERROR] Status: {res.status_code} - {res.text[:100]}", flush=True)
                except Exception as e:
                    print(f"[PROXY ERROR] {e}", flush=True)

    # الطريقة العادية (لمواقع أخرى أو في حال فشل الوسيط)
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
