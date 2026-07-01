# MCP (Model Context Protocol) Connect Guide

## Overview

Kubently runs an optional **MCP (Model Context Protocol) server** so that any MCP
client — Claude Desktop, Cursor, or your own custom agents — can delegate
Kubernetes troubleshooting to Kubently's agent.

The MCP server exposes a **single natural-language tool**, `ask_kubently`. The
calling agent passes a question (e.g. "why are pods crashlooping in payments?") and
Kubently's own troubleshooting agent investigates across the registered clusters —
running read-only kubectl as needed — and returns a synthesized answer. This mirrors
the [A2A interface](A2A_CONFIGURATION.md) over MCP transport: in both, **Kubently does
the reasoning**. The MCP server does *not* expose raw kubectl for the caller's LLM to
drive — that would bypass Kubently's troubleshooting loop, which is the whole value.
Both interfaces share the same authentication, session, and queue infrastructure.

## Endpoint & Transport

| Property | Value |
|----------|-------|
| Endpoint | `https://<your-kubently-host>/mcp/` |
| Transport | Streamable HTTP (FastMCP) |

The MCP server is served over **streamable HTTP** and is mounted at `/mcp/` on the
main Kubently API port (the same port that serves the REST API and `/a2a/`).

> **Trailing slash required.** Use `/mcp/`, not `/mcp` — same as `/a2a/`. A request to
> the bare `/mcp` returns an HTTP `307` redirect to `/mcp/`; most MCP clients follow it,
> but some HTTP clients drop the body/method on redirect, so always point at `/mcp/`.

For local development, this is typically `http://localhost:8080/mcp/` after a
port-forward:

```bash
kubectl port-forward -n kubently svc/kubently-api 8080:8080
```

## Authentication

All MCP requests **must** include the `X-API-Key` header:

```
X-API-Key: <your-api-key>
```

- The key is the **same API key** used by the Kubently CLI and the A2A protocol —
  i.e. a key from the `API_KEYS` environment variable.
- Requests without a valid key receive an HTTP `401` response:

  ```json
  {"error": "Unauthorized: valid X-API-Key required"}
  ```

See the [Secret Management](../CLAUDE.md#secret-management-best-practices) section
for how API keys are provisioned (`API_KEYS` / the `kubently-api-keys` secret).

## Enabling It

There is **no separate enable flag or environment variable** for the MCP server.
It is auto-mounted at API startup whenever the `mcp` Python SDK is installed,
which ships as part of the `a2a` extra.

On a successful mount, the API logs:

```
MCP server mounted at /mcp
```

If the SDK is not installed, the API logs the following and continues without the
MCP endpoint:

```
mcp package not installed; MCP server not mounted
```

> **Internal wiring**: the MCP server runs the same `KubentlyAgent` the A2A interface
> uses, in-process, sharing the same Redis-backed memory and queue. There is no extra
> configuration to wire up.

## Tools

The MCP server exposes exactly one tool:

| Tool | Signature | Description |
|------|-----------|-------------|
| `ask_kubently` | `ask_kubently(query: str, cluster_id: str = None, conversation_id: str = None) -> dict` | Routes a natural-language question through Kubently's troubleshooting agent and returns `{"answer": <markdown>, "thread_id": <id>}`. |

### Parameters

- **`query`** (required): the question or problem in plain language, e.g.
  `"why are pods crashlooping in the payments namespace on prod?"`.
- **`cluster_id`** (optional): target cluster id. Omit to let Kubently choose or ask;
  if you don't know the id, just name the cluster in `query`.
- **`conversation_id`** (optional): pass the `thread_id` from a previous response to
  continue the same troubleshooting thread (memory is preserved). Omit to start fresh —
  a new `thread_id` is generated and returned.

### Read-only safety

Read-only safety is enforced **downstream** by the executor command whitelist and
Kubernetes RBAC on the target cluster — the same enforcement the A2A agent relies on.
The agent only ever runs read-only kubectl.

## Connecting a Client

> **Caveat**: MCP remote-HTTP client config formats vary between clients and
> change over time. The examples below use the common `mcpServers` streamable-HTTP
> shape, but you should check your client's current documentation for the exact
> remote-server config syntax it expects.

### Generic streamable-HTTP MCP client

A typical streamable-HTTP MCP client configuration looks like this:

```json
{
  "mcpServers": {
    "kubently": {
      "type": "streamable-http",
      "url": "https://<your-kubently-host>/mcp/",
      "headers": {
        "X-API-Key": "<your-api-key>"
      }
    }
  }
}
```

### Claude Desktop / Cursor

Claude Desktop and Cursor read an `mcpServers` block (in their respective settings
/ `mcp.json` files). The same shape applies:

```json
{
  "mcpServers": {
    "kubently": {
      "type": "streamable-http",
      "url": "https://kubently.your-domain.com/mcp/",
      "headers": {
        "X-API-Key": "your-api-key"
      }
    }
  }
}
```

After saving the config, restart the client and confirm that the `ask_kubently`
tool appears in its tool list.

## Relationship to A2A

Kubently offers two ways for AI clients to connect, both backed by the same agent and
the same auth/session/queue infrastructure — **Kubently does the reasoning in both**:

| Interface | Endpoint | What it is |
|-----------|----------|------------|
| **A2A** | `/a2a/` | The agent over Google's A2A protocol; streams responses over SSE. Use when the client speaks A2A. |
| **MCP** | `/mcp` | The same agent behind a single `ask_kubently` tool; request/response. Use when the client speaks MCP. |

Pick whichever protocol your client already speaks. Neither exposes raw kubectl to the
caller — for direct cluster primitives, use the REST API (`/debug/execute`) instead.

## References

- [A2A Configuration Guide](A2A_CONFIGURATION.md) - The conversational agent interface
- [Agentgateway Setup](AGENTGATEWAY_SETUP.md) - Running a gateway in front of Kubently
- [Model Context Protocol](https://modelcontextprotocol.io/) - Official MCP specification
- [Kubently Documentation](../README.md)
