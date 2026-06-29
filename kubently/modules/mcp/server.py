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
