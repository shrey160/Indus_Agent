import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import httpx

ARXIV_API = "http://export.arxiv.org/api/query"
NS = {"atom": "http://www.w3.org/2005/Atom"}


def _normalize(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip())


async def arxiv_search(
    query: str,
    max_results: int = 5,
    days_back: int = 0,
) -> dict:
    """Search arXiv (no API key). Returns {query, results: [{title, authors, abstract, url, published}], source: 'arxiv'}."""
    params = {
        "search_query": f"all:{query}",
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": max_results,
    }
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        response = await client.get(ARXIV_API, params=params)
        response.raise_for_status()
        body = response.text

    root = ET.fromstring(body)
    cutoff = None
    if days_back > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    results = []
    for entry in root.findall("atom:entry", NS):
        published_text = entry.findtext("atom:published", default="", namespaces=NS)
        published_dt = None
        if published_text:
            try:
                published_dt = datetime.fromisoformat(published_text.replace("Z", "+00:00"))
            except Exception:
                pass

        if cutoff is not None and published_dt is not None and published_dt < cutoff:
            continue

        title = _normalize(entry.findtext("atom:title", default="", namespaces=NS))
        summary = _normalize(entry.findtext("atom:summary", default="", namespaces=NS))
        summary = summary[:1000]
        url = ""
        for link in entry.findall("atom:link", NS):
            if link.get("rel") == "alternate" and link.get("href"):
                url = link.get("href")
                break
        if not url:
            url = entry.findtext("atom:id", default="", namespaces=NS)

        authors = [
            _normalize(name.text)
            for name in entry.findall("atom:author/atom:name", NS)
        ]

        results.append({
            "title": title,
            "authors": authors,
            "abstract": summary,
            "url": url,
            "published": published_text,
        })

    return {"query": query, "results": results, "source": "arxiv"}
