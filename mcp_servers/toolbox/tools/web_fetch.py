from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

MAX_BODY_BYTES = 5 * 1024 * 1024  # 5 MB


async def web_fetch(url: str, max_chars: int = 8000) -> dict:
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "Local-AI-Hub/1.0"})
        response.raise_for_status()
        body = response.text[:MAX_BODY_BYTES]
        final_url = str(response.url)

    soup = BeautifulSoup(body, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cleaned = "\n".join(lines)
    truncated = len(cleaned) > max_chars

    return {
        "url": final_url,
        "title": title,
        "text": cleaned[:max_chars],
        "truncated": truncated,
        "source": url,
    }
