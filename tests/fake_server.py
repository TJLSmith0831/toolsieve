"""A tiny real MCP server, used as a downstream backend in tests.

    python fake_server.py <name>               # stdio
    python fake_server.py <name> --http 8123   # real HTTP on localhost
    python fake_server.py <name> --oauth 8123  # real HTTP, real OAuth 2.1 gate
"""

import sys

from fastmcp import FastMCP

# An access token the --oauth server accepts. Tests seed this into toolsieve's
# token store to reach the "already authorized" path without driving a browser;
# the server still validates it through its real auth middleware.
SEEDED_TOKEN = "seeded-access-token"


def _oauth_app(name: str, port: int) -> FastMCP:
    """A real OAuth-gated MCP server: 401 + WWW-Authenticate, PRM, ASM, DCR.

    fastmcp's own in-memory provider supplies the whole OAuth 2.1 surface, so
    the client under test performs genuine spec discovery rather than talking
    to a hand-rolled stub that only looks like an authorization server.
    """
    from mcp.server.auth.provider import AccessToken

    from fastmcp.server.auth.auth import ClientRegistrationOptions
    from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider

    base = f"http://127.0.0.1:{port}"
    provider = InMemoryOAuthProvider(
        base_url=base,
        resource_base_url=base,
        # Dynamic client registration on: it is how a client with no
        # pre-issued credentials joins, which is exactly toolsieve's case.
        client_registration_options=ClientRegistrationOptions(enabled=True),
    )
    provider.access_tokens[SEEDED_TOKEN] = AccessToken(
        token=SEEDED_TOKEN, client_id="seeded-client", scopes=[], expires_at=None
    )
    return FastMCP(name=name, auth=provider)


mcp = FastMCP(name=sys.argv[1] if len(sys.argv) > 1 else "fake")
if "--oauth" in sys.argv:
    mcp = _oauth_app(sys.argv[1], int(sys.argv[sys.argv.index("--oauth") + 1]))


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
    elif "--oauth" in sys.argv:
        mcp.run(
            transport="http",
            host="127.0.0.1",
            port=int(sys.argv[sys.argv.index("--oauth") + 1]),
        )
    else:
        mcp.run(transport="stdio")
