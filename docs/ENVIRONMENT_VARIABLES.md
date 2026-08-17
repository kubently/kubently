# Environment Variables Reference

## API Server Configuration

### Core Settings

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `API_HOST` | `0.0.0.0` | No | Host IP to bind the API server |
| `API_PORT` | `8080` | No | Port for the main API server |
| `PORT` | `8080` | No | Alias for API_PORT (for compatibility) |
| `LOG_LEVEL` | `INFO` | No | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `DEBUG` | `false` | No | Enable debug mode with auto-reload |

### Redis Configuration

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `REDIS_HOST` | `redis` | No | Redis server hostname |
| `REDIS_PORT` | `6379` | No | Redis server port |
| `REDIS_DB` | `0` | No | Redis database number |
| `REDIS_URL` | - | No | Full Redis URL (overrides individual settings) |

### Session Management

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `SESSION_TTL` | `3600` | No | Session TTL in seconds (1 hour) |
| `COMMAND_TIMEOUT` | `30` | No | Default command execution timeout in seconds |
| `MAX_COMMANDS_PER_FETCH` | `10` | No | Maximum commands per fetch operation |

### Authentication

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `API_KEYS` | - | Yes* | Comma-separated list of valid API keys |
| `AGENT_TOKEN_<ID>` | - | Yes* | Agent authentication tokens (per cluster) |

*Required for production deployments

### A2A (Agent-to-Agent) Configuration

**Note**: A2A is core functionality and is always enabled. It is mounted at `/a2a` on the main API port.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `A2A_EXTERNAL_URL` | - | No | External URL for A2A agent card (e.g., `https://api.example.com/a2a/`) |
| `KUBENTLY_MAX_FLEET_CLUSTERS` | `10` | No | Max clusters per `execute_kubectl_multi` fan-out call (each cluster adds up to ~4KB to the agent context) |
| `A2A_SERVER_DEBUG` | `false` | No | Enable A2A debug logging |

### Operator Runbooks (optional)

The agent loads hand-written markdown runbooks (YAML frontmatter with `name`
and `match` criteria: alert-name globs, namespace/workload selectors, topic
tags) from a directory and injects the best match(es) into investigations
whose text matches. In Helm deployments the directory is a ConfigMap built
from the `runbooks` values map; the store rescans it periodically (mtime +
size signature, like the executor command whitelist), so ConfigMap edits go
live without a pod restart.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `KUBENTLY_RUNBOOKS_DIR` | `/etc/kubently/runbooks` | No | Directory of `*.md` runbooks. Missing directory = feature off. Set automatically by Helm when `runbooks` values are provided |
| `KUBENTLY_RUNBOOKS_RELOAD_SECONDS` | `30` | No | Minimum seconds between directory rescans |
| `KUBENTLY_RUNBOOKS_MAX_CHARS` | `8000` | No | Cap on total injected runbook characters per investigation. Best match packs first; an oversized best match is truncated rather than dropped |

### Loki Log Search (optional)

When `LOKI_URL` is set on the API server, the agent registers the read-only `query_loki` tool (LogQL range queries) and injects Loki guidance into the system prompt. When unset (default), the tool does not exist and the prompt never mentions Loki. The selector-based `search_pod_logs` tool is always registered and needs no configuration.

The API never dials this URL itself — queries execute on each cluster's executor against the executor's own `LOKI_URL` (see Executor Configuration below). On the API side the variable only switches the tool on.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `LOKI_URL` | - | No | Presence enables the `query_loki` agent tool. Set via Helm `loki.url` |

### Change Correlation (optional sources)

The agent's `get_recent_changes` tool always aggregates rollout history,
ReplicaSet revisions and events through the executor's read-only kubectl
surface. Two additional change sources are configurable:

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `ARGOCD_URL` | - | No | Presence makes `get_recent_changes` include ArgoCD sync history. Set via Helm `changeCorrelation.argocd.url`. The API never dials this URL itself — queries execute on each cluster's executor against the executor's own `ARGOCD_URL` (see Executor Configuration below); on the API side the variable only switches the source on |

Helm release history has no API-side switch: the tool always asks, and each
executor answers with history or with a clear "not enabled" note (see
`HELM_HISTORY_ENABLED` under Executor Configuration).

### Conversation Memory (A2A Checkpointing)

The A2A agent persists multi-turn conversation state through a LangGraph
checkpointer. The backend is selectable so checkpointing works on Redis
servers without the RediSearch module (e.g. Upstash):

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `KUBENTLY_CHECKPOINTER_BACKEND` | `redisearch` | No | Checkpointer backend: `redisearch` (AsyncRedisSaver; requires the RediSearch module), `plain-redis` (core Redis commands only; works on Upstash and any managed Redis), `memory` (per-process, dev/test only), or `none` (disable cross-request memory) |
| `KUBENTLY_CHECKPOINT_TTL_SECONDS` | `604800` (7 days) | No | TTL for `plain-redis` checkpoint keys; refreshed on every checkpoint write, so active conversations never expire mid-flight. Set to `0` to disable expiry. Ignored by other backends |

Whatever the backend, initialization failure degrades gracefully: the agent
logs a warning and continues without cross-request memory, and single-request
diagnoses are unaffected.

### Prometheus Metrics Tool (optional)

When `PROMETHEUS_URL` is set on the API server, the agent registers the read-only `query_prometheus` tool and injects metrics guidance into the system prompt. When unset (default), the tool does not exist and the prompt never mentions metrics.

The API never dials this URL itself — queries execute on each cluster's executor against the executor's own `PROMETHEUS_URL` (see Executor Configuration below). On the API side the variable only switches the tool on.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `PROMETHEUS_URL` | - | No | Presence enables the `query_prometheus` agent tool. Set via Helm `prometheus.url` |

### LLM Configuration (for A2A)

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `LLM_PROVIDER` | `openai` | No | LLM provider (openai, anthropic, ollama) |
| `OPENAI_API_KEY` | - | If OpenAI | OpenAI API key |
| `OPENAI_ENDPOINT` | `https://api.openai.com/v1` | No | OpenAI API endpoint |
| `OPENAI_MODEL_NAME` | `gpt-4o` | No | OpenAI model to use |
| `OPENAI_MAX_TOKENS` | `4096` | No | Max completion tokens on the OpenAI path (parity with the Anthropic path). Note: previously unbounded — OpenAI-compatible brokers such as OpenRouter reserve `max_tokens` against the account balance per request, so an unbounded value can 402 on small balances. Raise it if long diagnoses are being truncated |
| `ANTHROPIC_API_KEY` | - | If Anthropic | Anthropic API key |
| `ANTHROPIC_MODEL_NAME` | `claude-3-5-sonnet-20241022` | No | Anthropic model to use |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | No | Ollama server URL |
| `OLLAMA_MODEL` | `llama3` | No | Ollama model to use |

### LangSmith Tracing (Production Observability)

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `LANGSMITH_TRACING` | `false` | No | Enable LangSmith tracing for observability |
| `LANGSMITH_API_KEY` | - | If tracing enabled | LangSmith API key (set via secret) |
| `POSTHOG_API_KEY` | - | No | Enables PostHog LLM observability (model, tokens, cost, latency per generation). Unset = no telemetry is collected or sent |
| `POSTHOG_HOST` | `https://us.i.posthog.com` | No | PostHog ingestion host (use `https://eu.i.posthog.com` for the EU region, or your own reverse proxy) |
| `LANGSMITH_PROJECT` | `default` | No | Project name in LangSmith UI |
| `LANGSMITH_ENDPOINT` | `https://api.smith.langchain.com` | No | LangSmith API endpoint |
| `LANGSMITH_SAMPLE_RATE` | `1.0` | No | Sampling rate (0.0-1.0) for trace volume reduction |

**See**: `docs/LANGSMITH_TRACING.md` for detailed setup guide

## Executor Configuration

### Core Settings

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `KUBENTLY_API_URL` | - | Yes | URL of the Kubently API server |
| `CLUSTER_ID` | - | Yes | Unique identifier for the cluster |
| `KUBENTLY_TOKEN` | - | Yes | Authentication token for the executor |
| `LOG_LEVEL` | `INFO` | No | Logging level |

### Whitelist Configuration

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `WHITELIST_PATH` | `/etc/kubently/whitelist.yaml` | No | Path to whitelist configuration |
| `REFRESH_INTERVAL` | `30` | No | Whitelist refresh interval in seconds |
| `TIMEOUT_SECONDS` | `30` | No | Command execution timeout |

### Log Search (search_pod_logs)

The executor performs selector-based log searches locally: pods are resolved and logs fetched through the same whitelist-enforced kubectl runner as ordinary commands, filtered on the executor, and only matching lines come back. All caps announce themselves in the tool output when they fire.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `LOG_SEARCH_MAX_PODS` | `20` | No | Max pods scanned per search (excess noted) |
| `LOG_SEARCH_MAX_MATCHES_PER_CONTAINER` | `50` | No | Max matching lines shown per container |
| `LOG_SEARCH_MAX_TOTAL_MATCHES` | `200` | No | Max matching lines shown per search |
| `LOG_SEARCH_MAX_LINE_CHARS` | `500` | No | Individual lines truncated beyond this |
| `LOG_SEARCH_MAX_OUTPUT_CHARS` | `20000` | No | Hard cap on assembled result size |
| `LOG_SEARCH_TIME_BUDGET` | `50` | No | Seconds of kubectl fetching before the search stops early (with a note) |

### Loki Log Search (optional)

The executor performs Loki queries locally (read-only GETs against `/loki/api/v1/query_range` only). The base URL comes exclusively from this local configuration — the control plane never supplies one. When unset (default), Loki queries are answered with a clear "not configured" error.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `LOKI_URL` | - | No | Loki base URL reachable from the executor pod, e.g. `http://loki.monitoring.svc.cluster.local:3100`. Set via Helm `loki.url` |
| `LOKI_TENANT_ID` | - | No | Optional `X-Scope-OrgID` header for multi-tenant Loki. Set via Helm `loki.tenantId` |
| `LOKI_TIMEOUT` | `30` | No | Query timeout in seconds |
| `LOKI_MAX_LINES` | `500` | No | Max log lines per query (the request `limit` is clamped to this) |
| `LOKI_MAX_LINE_CHARS` | `500` | No | Individual lines truncated beyond this |
| `LOKI_MAX_OUTPUT_CHARS` | `20000` | No | Hard cap on serialized result size |

### Prometheus Metrics Tool (optional)

The executor performs metric queries locally (read-only GETs against `/api/v1/query` and `/api/v1/query_range` only). The base URL comes exclusively from this local configuration — the control plane never supplies one. When unset (default), metric queries are answered with a clear "not configured" error.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `PROMETHEUS_URL` | - | No | Prometheus base URL reachable from the executor pod, e.g. `http://prometheus-operated.monitoring.svc.cluster.local:9090`. Set via Helm `prometheus.url` |
| `PROMETHEUS_TIMEOUT` | `30` | No | Query timeout in seconds |
| `PROMETHEUS_MAX_SERIES` | `50` | No | Max series returned per query (excess truncated with a note) |
| `PROMETHEUS_MAX_SAMPLES` | `2000` | No | Max total samples per range-query result (evenly downsampled with a note) |
| `PROMETHEUS_MAX_OUTPUT_CHARS` | `20000` | No | Hard cap on serialized result size |
The kubectl whitelist accepts the `rollout` verb in every mode, restricted in
code (immutably) to its read-only subcommands `history` and `status`;
`rollout restart/undo/pause/resume` are rejected in all modes.

### Helm Release History (optional, change correlation)

The executor runs read-only `helm history` / `helm list` locally (argv built
executor-side from validated fields — the control plane never sends raw
arguments). Opt-in because Helm 3 stores release records in Secrets: enabling
it in the Helm chart (`changeCorrelation.helmHistory.enabled`) also grants
the executor get/list on Secrets. The kubectl whitelist still blocks
`kubectl get secrets`; only helm's history/list output leaves the executor.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `HELM_HISTORY_ENABLED` | `false` | No | Set to `true` to allow read-only helm history/list. Set via Helm `changeCorrelation.helmHistory.enabled` |
| `HELM_TIMEOUT` | `30` | No | helm command timeout in seconds |
| `HELM_MAX_OUTPUT_CHARS` | `20000` | No | Hard cap on helm output size |

### ArgoCD Sync History (optional, change correlation)

The executor performs ArgoCD queries locally (read-only GETs against
`/api/v1/applications...` only). URL and token come exclusively from this
local configuration — the control plane never supplies either. When unset
(default), ArgoCD requests are answered with a clear "not configured" error.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `ARGOCD_URL` | - | No | ArgoCD API base URL reachable from the executor pod, e.g. `https://argocd-server.argocd.svc.cluster.local`. Set via Helm `changeCorrelation.argocd.url` |
| `ARGOCD_TOKEN` | - | No | Read-only ArgoCD API token (from a Secret via Helm `changeCorrelation.argocd.existingSecret`) |
| `ARGOCD_CA_CERT` | - | No | Path to a CA bundle for a self-signed argocd-server certificate (TLS verification stays on) |
| `ARGOCD_TIMEOUT` | `30` | No | Query timeout in seconds |
| `ARGOCD_MAX_OUTPUT_CHARS` | `20000` | No | Hard cap on serialized result size |

## Deployment-Specific Variables

### Kubernetes

When deploying to Kubernetes, these variables are typically set automatically:

| Variable | Set By | Description |
|----------|--------|-------------|
| `HOSTNAME` | Kubernetes | Pod hostname (used for routing) |
| `NAMESPACE` | Kubernetes | Current namespace |
| `POD_NAME` | Kubernetes | Current pod name |
| `NODE_NAME` | Kubernetes | Node where pod is running |

### Docker Compose

For local development with Docker Compose:

```env
# .env file example
REDIS_HOST=redis
REDIS_PORT=6379
API_PORT=8080
A2A_EXTERNAL_URL=http://localhost:8080/a2a/
LOG_LEVEL=DEBUG
API_KEYS=dev-key-1,dev-key-2
AGENT_TOKEN_LOCAL=local-dev-token
```

## Configuration Precedence

1. Environment variables (highest priority)
2. ConfigMap values (Kubernetes)
3. Default values in code (lowest priority)

## Security Considerations

### Sensitive Variables

The following variables contain sensitive data and should be stored in Kubernetes Secrets or secure vaults:

- `API_KEYS`
- `AGENT_TOKEN_*`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `KUBENTLY_TOKEN`

### Example Kubernetes Secret

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: kubently-secrets
type: Opaque
stringData:
  API_KEYS: "key1,key2,key3"
  AGENT_TOKEN_PROD: "secure-token-here"
  OPENAI_API_KEY: "sk-..."
```

## Environment-Specific Configurations

### Development

```bash
export API_PORT=8080
export LOG_LEVEL=DEBUG
export DEBUG=true
export REDIS_HOST=localhost
```

### Staging

```bash
export API_PORT=8080
export A2A_EXTERNAL_URL=https://kubently-staging.example.com/a2a/
export LOG_LEVEL=INFO
export REDIS_HOST=redis-staging
```

### Production

```bash
export API_PORT=8080
export A2A_EXTERNAL_URL=https://kubently.example.com/a2a/
export LOG_LEVEL=WARNING
export REDIS_HOST=redis-prod
export SESSION_TTL=7200  # 2 hours
```

## Troubleshooting

### Common Issues

1. **Redis connection errors**: Check `REDIS_HOST` and `REDIS_PORT`
2. **A2A not accessible**: A2A is always enabled. Ensure you're accessing it at the `/a2a` path on the main API port
3. **Authentication failures**: Verify `API_KEYS` and `AGENT_TOKEN_*` are set
4. **Wrong A2A URL in agent card**: Set `A2A_EXTERNAL_URL` correctly

### Debug Commands

```bash
# Check current environment in pod
kubectl exec deployment/kubently-api -- env | sort

# Check specific variables
kubectl exec deployment/kubently-api -- env | grep A2A

# Set environment variable
kubectl set env deployment/kubently-api KEY=value

# Remove environment variable
kubectl set env deployment/kubently-api KEY-
```
