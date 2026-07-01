"""
FastMCP server exposing Kubently's troubleshooting agent over MCP.

Any MCP client (Claude Desktop, Cursor, other agents) connects and gets ONE
natural-language tool: ask Kubently a question and its troubleshooting agent answers,
investigating across the registered fleet on the caller's behalf. This mirrors the A2A
surface over MCP transport rather than exposing raw kubectl, so Kubently's reasoning loop
stays in the loop instead of the caller's LLM driving kubectl directly. Read-only safety
is enforced downstream by the executor whitelist + RBAC.

This module imports the `mcp` SDK; callers should import it lazily so the API still
boots when the SDK isn't installed (see kubently/main.py).
"""

from mcp.server.fastmcp import FastMCP

from kubently.modules.mcp import tools


def build_mcp_server(redis_client=None) -> FastMCP:
    """Build the Kubently MCP server with its single ask tool registered.

    `redis_client` is passed to the agent for conversation memory (same wiring as A2A).
    One agent instance is reused across calls; `KubentlyAgent.run()` initializes lazily.
    """
    # Lazy import: the agent pulls in langchain/langgraph, which the API boots without
    # when the optional `a2a` extra isn't installed.
    from kubently.modules.a2a.protocol_bindings.a2a_server.agent import KubentlyAgent

    agent = KubentlyAgent(redis_client=redis_client)

    # streamable_http_path="/" so that mounting the app at "/mcp" in main.py yields a clean
    # "/mcp" endpoint (FastMCP's default internal path is "/mcp", which would give "/mcp/mcp").
    mcp = FastMCP("kubently", streamable_http_path="/")

    @mcp.tool()
    async def ask_kubently(
        query: str,
        cluster_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict:
        """Ask Kubently to troubleshoot a Kubernetes problem, in natural language.

        Kubently's agent investigates across the available clusters (running read-only
        kubectl as needed) and returns a synthesized answer — you do NOT issue kubectl
        yourself; describe the problem and let Kubently diagnose it.

        Args:
            query: The question or problem in plain language, e.g.
                   "why are pods crashlooping in the payments namespace on prod?".
            cluster_id: Optional target cluster id. Omit to let Kubently choose or ask;
                        if you don't know the id, just name the cluster in `query`.
            conversation_id: Optional id to continue a prior troubleshooting thread —
                             reuse the `thread_id` from a previous response. Omit to start fresh.

        Returns:
            {"answer": <markdown>, "thread_id": <id to continue the conversation>}
        """
        return await tools.ask_kubently(agent, query, cluster_id, conversation_id)

    return mcp


def add_api_key_auth(app, auth_module, public_well_known=False):
    """Wrap an ASGI app so it requires API-key auth — the same X-API-Key the CLI/A2A use.

    Returns a new ASGI app to MOUNT in place of `app`. This is a plain ASGI wrapper rather
    than Starlette `add_middleware`, because middleware added to a sub-app after it is built
    doesn't reliably run once the sub-app is mounted (the middleware stack is built lazily).
    The wrapper IS the mounted app, so it always runs — this is also the fix for the A2A
    mount, which had the same latent bypass.

    public_well_known=True leaves GET requests to a `/.well-known/...` path unauthenticated
    (the A2A agent card, which must stay publicly discoverable). Mounted sub-apps see the
    FULL path in scope["path"] with the mount prefix in scope["root_path"], so we compare
    the path relative to root_path.

    Note: generic ASGI helper that happens to live here; reused by main.py for both /mcp
    and /a2a. Move to modules/middleware if a third caller appears.
    """

    def _is_public(scope):
        if not public_well_known or scope.get("method") != "GET":
            return False
        path = scope.get("path", "")
        root = scope.get("root_path", "")
        rel = path[len(root):] if root and path.startswith(root) else path
        return rel.startswith("/.well-known/")

    async def asgi(scope, receive, send):
        if scope.get("type") != "http":
            await app(scope, receive, send)
            return

        if _is_public(scope):
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
