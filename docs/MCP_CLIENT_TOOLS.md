# External MCP Tools (MCP Client)

Kubently's diagnostic agent can consume tools from external [MCP](https://modelcontextprotocol.io/)
servers — e.g. Grafana Cloud's remote MCP (`https://mcp.grafana.com/mcp`) or
Datadog's remote MCP — alongside its native kubectl/log/metrics/change tools.
This is the *client* side of MCP; it is independent of Kubently's own `/mcp`
endpoint, which serves Kubently **as** an MCP server to other agents.

Only streamable-HTTP servers are supported (the transport remote/hosted MCPs
use). Tools register with an `mcp_<server>_` name prefix so they can never
collide with native tools or with each other across servers.

## Security model — read this before connecting anything

**Third-party MCP servers are untrusted input.** Kubently applies the same
skepticism it applies to alert payloads:

- Tool **descriptions** (which enter the model's context) are sanitized,
  length-capped, and prefixed with an explicit untrusted-source marker.
- Tool **results** are wrapped in `BEGIN/END UNTRUSTED MCP RESULT` markers and
  size-capped (`KUBENTLY_MCP_MAX_OUTPUT_CHARS`, default 20000 chars) with an
  explicit truncation note. The system prompt instructs the model to treat the
  contents as evidence, never as instructions.
- **Credentials** are sent only as HTTP headers to the configured URL. They are
  redacted from error messages, never logged, and never enter model context.

**Connect read-scoped servers and credentials only.** Kubently enforces
read-only kubectl through its executor whitelist, but it has no way to
constrain what a remote server's tools do — if you hand it a write-scoped
Grafana service account token, the model can mutate dashboards. Create
credentials with the narrowest read-only scope the vendor offers.

## Failure isolation

- A server that is unreachable when the agent starts contributes no tools
  (logged as a warning); the investigation proceeds with native tools.
- A server that fails or times out mid-investigation returns an error message
  to the model (never an exception), and the prompt tells the model to
  continue with native tools rather than retry-loop.

## Static configuration (Helm)

```yaml
# values.yaml
mcpServers:
  - name: grafana
    url: https://mcp.grafana.com/mcp
    existingSecret: kubently-grafana-mcp   # secret holding the bearer token
    secretKey: token                       # key within the secret (default "token")
  - name: datadog
    url: https://mcp.datadoghq.com/api/unstable/mcp
    headers:                               # optional NON-secret headers only
      X-Some-Header: value
```

```bash
kubectl create secret generic kubently-grafana-mcp \
  --from-literal=token="glsa_..." -n kubently
```

The chart renders the server list (minus tokens) into `KUBENTLY_MCP_SERVERS`
and wires each `existingSecret` to a per-server env var (`MCP_TOKEN_<NAME>`)
that the config references via `bearer_token_env` — tokens never appear in the
rendered JSON or in values files.

## Static configuration (env / file)

Outside Helm, set `KUBENTLY_MCP_SERVERS` (inline JSON list) or
`KUBENTLY_MCP_SERVERS_FILE` (YAML/JSON file; a bare list or a `servers:` key):

```yaml
servers:
  - name: grafana
    url: https://mcp.grafana.com/mcp
    bearer_token_env: GRAFANA_MCP_TOKEN      # preferred: token stays in the env
  - name: datadog
    url: https://mcp.datadoghq.com/api/unstable/mcp
    headers_env:                             # API-key-style credential headers
      DD-API-KEY: DATADOG_API_KEY_ENV_VAR
      DD-APPLICATION-KEY: DATADOG_APP_KEY_ENV_VAR
```

Entry fields: `name`, `url` (required); `bearer_token_env` /
`bearer_token` (Authorization: Bearer); `headers` (plain); `headers_env`
(header → env var name). Invalid entries are skipped with a warning; the rest
still load. See `docs/ENVIRONMENT_VARIABLES.md` for the timeout/size-cap
variables.

## Per-request injection (embedding services)

Services that embed the agent (e.g. a multi-tenant control plane brokering
per-tenant OAuth) can supply servers **per invocation** instead of, or in
addition to, the static config:

```python
from kubently.modules.a2a.protocol_bindings.a2a_server.agent import KubentlyAgent
from kubently.modules.a2a.protocol_bindings.a2a_server.mcp_client import MCPServerSpec

agent = KubentlyAgent(redis_client=redis)
async for event in agent.run(
    messages=[{"role": "user", "content": "why is checkout slow?"}],
    thread_id="tenant-42:incident-7",
    mcp_servers=[
        MCPServerSpec(
            name="grafana",
            url="https://mcp.grafana.com/mcp",
            headers={"Authorization": "Bearer <tenant-scoped token>"},
            secret_values=["<tenant-scoped token>"],  # enables redaction
        ),
        # plain dicts of the same shape are also accepted:
        {"name": "datadog", "url": "https://...", "bearer_token": "<token>"},
    ],
):
    ...
```

Contract:

- The specs (and the credentials inside them) live only for the duration of
  the `run()` call — the engine never stores them.
- Tools from per-request servers exist only for that invocation and are
  announced to the model in a context note carrying the same untrusted-data
  warning as the static prompt guidance.
- Unreachable per-request servers degrade exactly like static ones.
