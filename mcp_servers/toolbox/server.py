import os

from fastmcp import FastMCP

from tools.rag import rag_ingest_text, rag_search
from tools.util_datetime import get_datetime
from tools.web_fetch import web_fetch
from tools.web_search import web_search

mcp = FastMCP("chat-toolbox")


@mcp.tool(name="web.search")
async def web_search_tool(query: str, max_results: int = 5) -> dict:
    """Search the web via local SearXNG. Returns {query, results: [{title, url, snippet}], source}."""
    return await web_search(query, max_results)


@mcp.tool(name="web.fetch")
async def web_fetch_tool(url: str, max_chars: int = 8000) -> dict:
    """Fetch a URL and return cleaned readable text. Returns {url, title, text, truncated, source}."""
    return await web_fetch(url, max_chars)


@mcp.tool(name="util.datetime")
async def util_datetime_tool(timezone: str = "local") -> dict:
    """Current date/time. Always call before reasoning about 'latest/today/recent'."""
    return await get_datetime(timezone)


@mcp.tool(name="rag.search")
async def rag_search_tool(query: str, top_k: int = 5, min_score: float = 0.5) -> dict:
    """Semantic search over the user's uploaded documents.
    Returns {query, results: [{doc, chunk_id, snippet, score}], source: 'rag'}.
    """
    return await rag_search(query, top_k, min_score)


@mcp.tool(name="rag.ingest")
async def rag_ingest_tool(text: str, title: str) -> dict:
    """Index an ad-hoc text note into the document store for later rag.search."""
    return await rag_ingest_text(text, title)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "9000"))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
