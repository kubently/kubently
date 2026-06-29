"""
FastMCP server exposing Kubently's multi-cluster troubleshooting over MCP.

Any MCP client (Claude Desktop, Cursor, other agents) can connect and get tools to
list clusters and run read-only kubectl against any registered cluster — the same
fleet the A2A agent reaches. Read-only safety is enforced downstream by the executor
whitelist + RBAC.

This module imports the `mcp` SDK; callers should import it lazily so the API still
boots when the SDK isn't installed (see kubently/main.py).
"""

import os

from mcp.server.fastmcp import FastMCP

from kubently.modules.mcp import tools


def _creds() -> tuple[str, str]:
    """Resolve the internal API URL + key at call time (same pattern as the A2A agent)."""
    from kubently.modules.auth import AuthModule

    api_url = os.getenv("KUBENTLY_API_URL", "http://localhost:8080")
    api_key = AuthModule.extract_first_api_key()
    return api_url, api_key


def build_mcp_server() -> FastMCP:
    """Build the Kubently MCP server with its tools registered."""
    # streamable_http_path="/" so that mounting the app at "/mcp" in main.py yields a clean
    # "/mcp" endpoint (FastMCP's default internal path is "/mcp", which would give "/mcp/mcp").
    mcp = FastMCP("kubently", streamable_http_path="/")

    @mcp.tool()
    async def list_clusters() -> list[str]:
        """List the Kubernetes clusters currently available for troubleshooting."""
        api_url, api_key = _creds()
        return await tools.list_clusters(api_url, api_key)

    @mcp.tool()
    async def execute_kubectl(cluster_id: str, command: str, namespace: str = "default") -> str:
        """Run a read-only kubectl command against a cluster.

        Args:
            cluster_id: Target cluster (use list_clusters to discover IDs).
            command: kubectl command without the leading `kubectl`, e.g. "get pods -o wide".
            namespace: Namespace to use when the command doesn't specify one.
        """
        api_url, api_key = _creds()
        return await tools.execute_kubectl(api_url, api_key, cluster_id, command, namespace)

    return mcp


def add_api_key_auth(app, auth_module):
    """Wrap an ASGI app so it requires API-key auth — the same X-API-Key the CLI/A2A use.

    Returns a new ASGI app to MOUNT in place of `app`. This is a plain ASGI wrapper rather
    than Starlette `add_middleware`, because middleware added to a sub-app after
    `streamable_http_app()` doesn't reliably run once the sub-app is mounted (the middleware
    stack is built lazily). The wrapper is the mounted app, so it always runs.
    """

    async def asgi(scope, receive, send):
        if scope.get("type") != "http":
            await app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        api_key = headers.get("x-api-key")
        valid = False
        if api_key:
            valid, _ = await auth_module.verify_api_key(api_key)

        if not valid:
            from starlette.responses import JSONResponse

            await JSONResponse({"error": "Unauthorized: valid X-API-Key required"}, status_code=401)(
                scope, receive, send
            )
            return

        await app(scope, receive, send)

    return asgi
