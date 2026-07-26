"""A tiny real MCP server, used as a downstream backend in tests.

    python fake_server.py <name>              # stdio
    python fake_server.py <name> --http 8123  # real HTTP on localhost
"""

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
    if "--http" in sys.argv:
        from fastmcp.server.dependencies import get_http_headers

        @mcp.tool
        def echo_auth() -> str:
            """Return the Authorization header this request arrived with."""
            # include= is required: fastmcp strips `authorization` by default.
            return get_http_headers(include={"authorization"}).get("authorization", "<none>")

        mcp.run(transport="http", host="127.0.0.1", port=int(sys.argv[sys.argv.index("--http") + 1]))
    else:
        mcp.run(transport="stdio")
