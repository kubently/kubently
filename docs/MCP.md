# MCP (Model Context Protocol) Connect Guide

## Overview

Kubently runs an optional **MCP (Model Context Protocol) server** so that any MCP
client — Claude Desktop, Cursor, or your own custom agents — gets the same
read-only, multi-cluster Kubernetes troubleshooting that the A2A agent has.

Where the [A2A interface](A2A_CONFIGURATION.md) is a full conversational agent you
talk to in natural language, the MCP server exposes a small set of **tools** that
an MCP client drives directly. Both interfaces share the same authentication,
session, and queue infrastructure, so connecting over MCP gives your client
direct access to Kubently's cluster tooling without a separate deployment.

## Endpoint & Transport

| Property | Value |
|----------|-------|
| Endpoint | `https://<your-kubently-host>/mcp` |
| Transport | Streamable HTTP (FastMCP) |

The MCP server is served over **streamable HTTP** and is mounted at `/mcp` on the
main Kubently API port (the same port that serves the REST API and `/a2a/`).

For local development, this is typically `http://localhost:8080/mcp` after a
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

> **Internal wiring**: the MCP tools call the Kubently API internally using the
> URL in `KUBENTLY_API_URL` (default `http://localhost:8080`). You normally do not
> need to set this.

## Tools

The MCP server exposes exactly two tools:

| Tool | Signature | Description |
|------|-----------|-------------|
| `list_clusters` | `list_clusters() -> list[str]` | Returns the IDs of the clusters currently available for troubleshooting. Adapter over `GET /debug/clusters`. |
| `execute_kubectl` | `execute_kubectl(cluster_id: str, command: str, namespace: str = "default") -> str` | Runs a read-only kubectl command against the given cluster and returns its output. Adapter over `POST /debug/execute`. |

### `command` format

For `execute_kubectl`, the `command` argument is the kubectl command **without** the
leading `kubectl`. The first word is the verb/command type and the rest are
arguments. For example:

```
get pods -o wide
```

### Read-only safety

Read-only safety is **not** enforced in the MCP layer. The MCP tools do not
validate or filter commands. Read-only behavior is enforced **downstream** by the
executor command whitelist and Kubernetes RBAC on the target cluster. Treat the
MCP layer as a transport/adapter, not a policy boundary.

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
      "url": "https://<your-kubently-host>/mcp",
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
      "url": "https://kubently.your-domain.com/mcp",
      "headers": {
        "X-API-Key": "your-api-key"
      }
    }
  }
}
```

After saving the config, restart the client and confirm that the `list_clusters`
and `execute_kubectl` tools appear in its tool list.

## Relationship to A2A

Kubently offers two ways for AI clients to connect, both backed by the same
auth/session/queue infrastructure:

| Interface | Endpoint | What it is |
|-----------|----------|------------|
| **A2A** | `/a2a/` | A full agent you talk to in natural language; it plans and runs the kubectl tooling itself and streams responses over SSE. |
| **MCP** | `/mcp` | A set of tools (`list_clusters`, `execute_kubectl`) that any MCP client drives directly — the calling client does the reasoning. |

Use A2A when you want Kubently to do the reasoning and troubleshooting
conversationally. Use MCP when you have your own agent or client that wants direct
tool access to clusters.

## References

- [A2A Configuration Guide](A2A_CONFIGURATION.md) - The conversational agent interface
- [Agentgateway Setup](AGENTGATEWAY_SETUP.md) - Running a gateway in front of Kubently
- [Model Context Protocol](https://modelcontextprotocol.io/) - Official MCP specification
- [Kubently Documentation](../README.md)
