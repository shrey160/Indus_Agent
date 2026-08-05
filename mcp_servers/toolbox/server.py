import os

from fastmcp import FastMCP
from starlette.responses import JSONResponse

from tools.util_datetime import get_datetime
from tools.web_fetch import web_fetch
from tools.web_search import web_search

mcp = FastMCP("chat-toolbox")


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    return JSONResponse({"status": "ok"})


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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "9000"))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
