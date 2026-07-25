"""A tiny real stdio MCP server, used as a downstream backend in tests."""

import sys

from fastmcp import FastMCP

mcp = FastMCP(name=sys.argv[1] if len(sys.argv) > 1 else "fake")


@mcp.tool
def get_weather(city: str) -> str:
    """Get the current weather forecast, temperature and rain, for a city."""
    return f"sunny in {city}"


@mcp.tool
def search_docs(query: str) -> str:
    """Search the project's markdown documentation files for a phrase."""
    return f"docs matching {query}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
