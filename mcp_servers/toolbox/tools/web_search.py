import os
import urllib.parse

import httpx

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://searxng:8080")


async def web_search(query: str, max_results: int = 5) -> dict:
    params = {"q": query, "format": "json"}
    url = f"{SEARXNG_URL}/search?{urllib.parse.urlencode(params)}"
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        response = await client.get(url, headers={"Accept": "application/json"})
        response.raise_for_status()
        data = response.json()

    raw_results = data.get("results", []) or []
    limited = raw_results[:max_results]
    results = [
        {
            "title": item.get("title"),
            "url": item.get("url"),
            "snippet": item.get("content") or item.get("abstract"),
        }
        for item in limited
    ]
    return {"query": query, "results": results, "source": "searxng"}
