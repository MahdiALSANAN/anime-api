
import asyncio
from curl_cffi.requests import AsyncSession


URL = "https://witanime.site/"


async def main():
    print("=" * 50)
    print("[TEST] WitAnime connection")
    print("=" * 50)

    try:
        async with AsyncSession(
            impersonate="chrome124",
            timeout=25,
        ) as session:

            response = await session.get(
                URL,
                headers={
                    "Accept": (
                        "text/html,application/xhtml+xml,"
                        "application/xml;q=0.9,*/*;q=0.8"
                    ),
                    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
                },
            )

            print("Status:", response.status_code)
            print("Final URL:", response.url)
            print("Server:", response.headers.get("server"))
            print("CF-Ray:", response.headers.get("cf-ray"))
            print(
                "Content-Type:",
                response.headers.get("content-type"),
            )

            print("\nResponse preview:")
            print(response.text[:1000])

            if response.status_code == 200:
                print("\n[RESULT] Website returned HTTP 200")

            elif response.status_code == 403:
                print("\n[RESULT] Access denied (HTTP 403)")

            else:
                print(
                    "\n[RESULT] HTTP status:",
                    response.status_code,
                )

    except Exception as error:
        print("\n[ERROR]")
        print(type(error).__name__, str(error))


if __name__ == "__main__":
    asyncio.run(main())
