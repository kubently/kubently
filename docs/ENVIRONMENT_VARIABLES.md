# Environment Variables Reference

## API Server Configuration

### Core Settings

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `API_HOST` | `0.0.0.0` | No | Host IP the API server binds (see the container note below) |
| `API_PORT` | `8080` | No | Port the API server binds (see the container note below) |
| `LOG_LEVEL` | `INFO` | No | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `DEBUG` | `false` | No | Enables uvicorn auto-reload (see the container note below) |

**Container note**: the published API image starts the server with a fixed
command — `uvicorn kubently.main:app --host 0.0.0.0 --port 8080`
(`deployment/docker/api/Dockerfile`). `API_HOST`, `API_PORT` and `DEBUG` are
only consulted by the `if __name__ == "__main__"` block in `kubently/main.py`,
so in a Kubernetes or Docker deployment they do **not** move the listening
address. The Helm chart sets `PORT` and `API_PORT` under `api.env`; neither is
read by application code today. To change what clients connect to in-cluster,
change `api.service.port`.

### Redis Configuration

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `REDIS_HOST` | `kubently-redis-master` | No | Redis server hostname |
| `REDIS_PORT` | `6379` | No | Redis server port. A `tcp://host:port` value (as injected by Kubernetes service links) is accepted — the port is parsed out of it |
| `REDIS_DB` | `0` | No | Redis database number |
| `REDIS_PASSWORD` | - | No | Password for an authenticated Redis. Passed as a connection parameter (not embedded in the URL), so special characters need no escaping. The chart wires it from `redis.auth.existingSecret` |

The API builds its connection as `redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}`
with `REDIS_PASSWORD` supplied separately. The chart also sets a `REDIS_URL`
variable on the API pod, but the only code that reads it is
`kubently/modules/storage/__init__.py`, which nothing in the API server
imports — changing it has no effect on the running API. `REDIS_HOST` is
rendered by the chart as `{release-name}-redis-master`, which is why the
in-code default is `kubently-redis-master`.

### Session Management

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `SESSION_TTL` | `3600` | No | Session TTL in seconds. Note the Helm chart overrides this to `300` under `api.env` |
| `COMMAND_TIMEOUT` | `30` | No | Default command execution timeout in seconds |
| `MAX_COMMANDS_PER_FETCH` | `10` | No | Maximum commands per fetch operation |

### Authentication

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `API_KEYS` | - | Yes | Valid API keys, comma- **or** newline-separated. Both `service:key` and bare `key` forms are accepted. Startup fails with a clear error if unset — there is no default |
| `REQUIRE_AUTH` | `true` | No | Parsed into the auth config but not currently consulted by any request path; authentication is always enforced |

**There is no `AGENT_TOKEN_<ID>` / `EXECUTOR_TOKEN_<ID>` environment variable.**
Executor tokens live in Redis under `executor:token:{cluster_id}` and are
created through the admin API (`POST /admin/agents/{cluster_id}/token`, which
accepts an auto-generated or a caller-supplied 32–128 character token). The
executor side of the same token is `KUBENTLY_TOKEN` (see Executor
Configuration).

### OIDC / OAuth (optional)

Off by default. When enabled, the API exposes auth discovery and validates
bearer tokens alongside API keys — see `docs/AUTH_DISCOVERY.md` and
`docs/OAUTH_USAGE.md` for the full flow.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `OIDC_ENABLED` | `false` | No | Master switch for OIDC/OAuth support |
| `OIDC_ISSUER` | - | If enabled | Issuer URL; the endpoints below default to `{issuer}/jwks`, `{issuer}/token`, `{issuer}/device/code` when unset |
| `OIDC_CLIENT_ID` | `kubently-cli` | No | Client id presented by the CLI |
| `OIDC_AUDIENCE` | value of `OIDC_CLIENT_ID` | No | Expected token audience |
| `OIDC_JWKS_URI` | `{OIDC_ISSUER}/jwks` | No | JWKS endpoint override |
| `OIDC_TOKEN_ENDPOINT` | `{OIDC_ISSUER}/token` | No | Token endpoint override |
| `OIDC_DEVICE_AUTH_ENDPOINT` | `{OIDC_ISSUER}/device/code` | No | Device-authorization endpoint override |
| `OIDC_SCOPES` | `openid email profile groups` | No | Space-separated scope list |

### A2A (Agent-to-Agent) Configuration

**Note**: A2A is core functionality and is always enabled. It is mounted at `/a2a` on the main API port.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `A2A_EXTERNAL_URL` | - | No | External URL for A2A agent card (e.g., `https://api.example.com/a2a/`) |
| `KUBENTLY_MAX_FLEET_CLUSTERS` | `10` | No | Max clusters per `execute_kubectl_multi` fan-out call (each cluster adds up to ~4KB to the agent context) |
| `A2A_SERVER_DEBUG` | `false` | No | Enable A2A debug logging |
| `KUBENTLY_MAX_OUTPUT_CHARS` | `20000` | No | Per-cluster output cap applied to kubectl results before they enter the agent's context |
| `KUBENTLY_PROMPT_FILE` | - | No | Explicit path to a prompt YAML file, overriding the role-based lookup under `/etc/kubently/prompts/` |

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

### Incident History (institutional memory)

When an investigation concludes with a root cause, the agent persists a
compact incident record (timestamp, cluster, resources involved, symptom
keywords, root-cause one-liner, resolution when stated) to Redis. Past
incidents are retrievable through the `search_past_incidents` agent tool,
and a strong match against a new investigation auto-surfaces a one-line
"similar past incident" note (framed as context to verify, never a
conclusion). Records are stored per authenticated caller using the same
namespace derivation as conversation-memory thread ids, so incidents never
cross tenant boundaries. Core Redis commands only — works on managed Redis
without RediSearch. On by default; requires a Redis connection.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `KUBENTLY_INCIDENT_HISTORY` | `true` | No | Kill switch. `false` disables recording, the `search_past_incidents` tool, and auto-surfacing |
| `KUBENTLY_INCIDENT_TTL_SECONDS` | `7776000` (90 days) | No | TTL for incident records. Set to `0` to disable expiry |
| `KUBENTLY_INCIDENT_MAX_PER_NAMESPACE` | `200` | No | Per-caller-namespace record cap; oldest records are evicted beyond it |
| `KUBENTLY_INCIDENT_SURFACE_MIN_SCORE` | `40` | No | Minimum match score before a past incident is auto-surfaced into a new investigation. Raise to surface less often; the search tool is unaffected |

In Helm deployments set these under `api.env`. Recording and retrieval
failures are logged and skipped — incident history can never break an
investigation or a response.

### Cloud Telemetry Tools (optional)

The agent's three cloud tools — `query_cloud_logs`, `query_cloud_metrics` and
`get_recent_cloud_changes` — are registered whenever cloud tooling is not
explicitly switched off, but each one checks, per target cluster and per call,
that the cluster's executor actually reports a cloud identity. A fleet with no
cloud-enabled executors therefore sees the tools refuse every call rather than
silently return nothing. The API holds no cloud credential of its own: it
forwards whitelisted operations to the executor, which uses its pod's workload
identity (see `KUBENTLY_CLOUD_MODE` under Executor Configuration and
`docs/CLOUD_TELEMETRY.md`).

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `KUBENTLY_CLOUD_TOOLS` | `auto` | No | Set to `off` to skip registering the cloud tools entirely. Any other value (including the default) registers them, subject to the per-call executor capability check |

### Prometheus Metrics Tool (optional)

When `PROMETHEUS_URL` is set on the API server, the agent registers the read-only `query_prometheus` tool and injects metrics guidance into the system prompt. When unset (default), the tool does not exist and the prompt never mentions metrics.

The API never dials this URL itself — queries execute on each cluster's executor against the executor's own `PROMETHEUS_URL` (see Executor Configuration below). On the API side the variable only switches the tool on.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `PROMETHEUS_URL` | - | No | Presence enables the `query_prometheus` agent tool. Set via Helm `prometheus.url` |

### GitOps PR Remediation (optional, default OFF)

When ALL of `KUBENTLY_GITOPS_PROVIDER`, `KUBENTLY_GITOPS_REPO` and `KUBENTLY_GITOPS_TOKEN` are set on the API server, the agent registers two tools — `get_manifest_file` (read-only repo fetch) and `propose_fix_pr` (branch + commit + pull request with the investigation evidence, clearly marked machine-proposed) — and injects the matching prompt guidance. A partial configuration logs a warning and stays off; when off, the prompt never mentions the tools. The agent can only **propose**: it has no merge capability, and cluster access remains read-only. See `docs/GITOPS_REMEDIATION.md`.

These run on the API server only — executors never hold the Git token.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `KUBENTLY_GITOPS_PROVIDER` | - | For the feature | `github` or `gitlab`. Set via Helm `gitRemediation.provider` |
| `KUBENTLY_GITOPS_REPO` | - | For the feature | Manifests repo: GitHub `owner/repo`, GitLab full project path. Set via Helm `gitRemediation.repo` |
| `KUBENTLY_GITOPS_TOKEN` | - | For the feature | Git token, from a manually created secret (Helm `gitRemediation.existingSecret`). Never exposed to the LLM, tool output, or traces |
| `KUBENTLY_GITOPS_BASE_BRANCH` | `main` | No | Branch proposals are opened against. Set via Helm `gitRemediation.baseBranch` |
| `KUBENTLY_GITOPS_MAX_FILES` | `5` | No | Max files per proposed PR; larger proposals are refused |
| `KUBENTLY_GITOPS_MAX_LINES` | `200` | No | Max changed lines (diff-measured) per proposed PR; larger proposals are refused |
| `KUBENTLY_GITOPS_API_BASE` | provider default | No | API base for GitHub Enterprise (`https://ghe.example.com/api/v3`) or self-hosted GitLab (`https://gitlab.example.com/api/v4`) |

**Security — scope the token to the manifests repo only.** Use a GitHub fine-grained PAT granted to the ONE manifests repository (Contents: read/write, Pull requests: read/write) or a GitLab project access token (Developer role, `api` scope) on the ONE project. Never a user-wide or org-wide token: the token defines the blast radius of a bad proposal. Protect the base branch so merging always requires a human review enforced by the Git host, and create the secret manually (`kubectl create secret generic kubently-gitops-token --from-literal=token=...`) rather than putting the token in values files.
### External MCP Servers (optional)

When MCP servers are configured, the agent registers each server's tools under
an `mcp_<server>_` prefix and injects external-tool guidance into the system
prompt. When unconfigured (default), no external tools exist and the prompt
never mentions them. Third-party tool descriptions and results are treated as
untrusted input: sanitized, framed, and size-capped. An unreachable server
degrades to "tools unavailable" — investigations proceed with native tools.

**Connect read-scoped servers/credentials only.** Kubently cannot enforce
read-only semantics on a remote MCP server's tools. See
`docs/MCP_CLIENT_TOOLS.md` for the full configuration and security guide.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `KUBENTLY_MCP_SERVERS` | - | No | Inline JSON list of servers: `[{"name": "grafana", "url": "https://mcp.grafana.com/mcp", "bearer_token_env": "MCP_TOKEN_GRAFANA"}]`. Set via Helm `mcpServers` (which also wires each token secret to the named env var). Takes precedence over the file below |
| `KUBENTLY_MCP_SERVERS_FILE` | - | No | Path to a YAML/JSON file with the same entries (a bare list, or under a `servers:` key) |
| `KUBENTLY_MCP_MAX_OUTPUT_CHARS` | `20000` | No | Per-result size cap for external MCP tool output; truncation is noted in the result |
| `KUBENTLY_MCP_CONNECT_TIMEOUT` | `15` | No | Seconds to wait per server when listing tools at agent startup |
| `KUBENTLY_MCP_TOOL_TIMEOUT` | `60` | No | Seconds to wait per external tool call before returning a timeout error to the model |

Server entry fields: `name` + `url` (required, streamable HTTP);
`bearer_token_env` (env var holding a bearer token — preferred) or
`bearer_token` (literal, discouraged); `headers` (plain, non-secret);
`headers_env` (map of header name → env var, for API-key-style credentials).
Credentials are sent only to the configured URL and are redacted from errors
and logs.

### Proactive Operation (Slack notifications, deploy verification, scheduled checks)

One Slack incoming-webhook URL powers every proactive path: Alertmanager
diagnoses (`/webhooks/alertmanager`), the fleet health digest
(`/webhooks/fleet-report`), deployment verifications
(`/webhooks/verify-deployment`) and scheduled checks
(`/webhooks/scheduled-check`). The URL is a credential — set it via the Helm
`api.slackWebhook.existingSecret` reference rather than a values file.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `SLACK_WEBHOOK_URL` | - | For posting paths | Slack incoming-webhook URL all proactive results post to. `dry_run` calls never need it. Set via Helm `api.slackWebhook` |
| `KUBENTLY_FLEET_REPORT_PROMPT_FILE` | `/etc/kubently/prompts/fleet_report.prompt.yaml` | No | Fleet digest query file (rendered from Helm `fleetReport.query`) |
| `KUBENTLY_CHECKS_FILE` | `/etc/kubently/checks/checks.yaml` | No | Scheduled-checks config file (rendered from Helm `scheduledChecks.checks`, read per request — no restart on change) |
| `KUBENTLY_VERIFY_TIMEOUT_SECONDS` | `600` | No | Default rollout settle deadline for `/webhooks/verify-deployment` when the request doesn't send `timeout_seconds` (per-request values clamp to 60–1800) |
| `KUBENTLY_VERIFY_WATCH_SECONDS` | `0` (off) | No | Enables the deploy watch: sweep interval in seconds (min 15) for finding `kubently.io/verify=enabled` workloads whose generation changed. Set via Helm `verifyDeployment.watch` |
| `KUBENTLY_VERIFY_WATCH_CLUSTERS` | - (all registered) | No | Comma-separated cluster ids to restrict the deploy watch sweep to |

### LLM Configuration (for A2A)

`LLM_PROVIDER` has **no default and is required**: the agent raises
`Unsupported LLM_PROVIDER ...` at construction time if it is unset or
unrecognised. The Helm chart does not set it either — supply it under
`api.env` (`deployment/helm/test-values.yaml` and the `kubently install` CLI
both do). Matching is substring-based on the lowercased value:

| Value contains | Provider used | Model variable |
|----------------|---------------|----------------|
| `anthropic` or `claude` | Anthropic (`ChatAnthropic`) | `ANTHROPIC_MODEL_NAME` |
| `openai` or `azure` | OpenAI-compatible (`ChatOpenAI`) | `OPENAI_MODEL_NAME` |
| `google` or `gemini` | Google Gemini (`ChatGoogleGenerativeAI`) | `GOOGLE_MODEL_NAME` |

The conventional values are `anthropic-claude`, `openai` and `google-gemini`.
Ollama is **not** supported — there is no Ollama code path.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `LLM_PROVIDER` | - | Yes | Provider selector (see table above). Unset or unrecognised = the agent fails to start |
| `OPENAI_API_KEY` | - | If OpenAI | OpenAI API key. Read by the LangChain OpenAI client, not by Kubently code |
| `OPENAI_MODEL_NAME` | `gpt-4o` | No | OpenAI model to use |
| `OPENAI_MAX_TOKENS` | `4096` | No | Max completion tokens on the OpenAI path (parity with the Anthropic path). Note: previously unbounded — OpenAI-compatible brokers such as OpenRouter reserve `max_tokens` against the account balance per request, so an unbounded value can 402 on small balances. Raise it if long diagnoses are being truncated |
| `ANTHROPIC_API_KEY` | - | If Anthropic | Anthropic API key. Read by the LangChain Anthropic client, not by Kubently code |
| `ANTHROPIC_MODEL_NAME` | `claude-sonnet-4-6` | No | Anthropic model to use |
| `ANTHROPIC_CONTEXT_CLEARING` | `true` | No | Anthropic path only. When `true`, the model is created with the `context-management-2025-06-27` beta and `clear_tool_uses_20250919` edits so long investigations do not overflow the context window. Set to `false` for a plain `ChatAnthropic` client |
| `GOOGLE_API_KEY` | - | If Google | Google Gemini API key. Read by the LangChain Google client, not by Kubently code |
| `GOOGLE_MODEL_NAME` | `gemini-2.0-flash` | No | Gemini model to use |

The Helm chart wires `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`
and `LANGSMITH_API_KEY` from a secret named `kubently-llm-secrets` (the name is
fixed in `api-deployment.yaml`); every key is optional, so the secret only
needs the provider you actually use.

### LangSmith Tracing (Production Observability)

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
The `LANGSMITH_*` variables are consumed by the LangSmith/LangChain SDK, not by
Kubently code — Kubently only passes them through the pod environment, so their
exact semantics follow the SDK. The chart wires `LANGSMITH_API_KEY` from the
`kubently-llm-secrets` secret; set the rest under `api.env`.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `LANGSMITH_TRACING` | `false` | No | Enable LangSmith tracing for observability |
| `LANGSMITH_API_KEY` | - | If tracing enabled | LangSmith API key (set via secret) |
| `LANGSMITH_PROJECT` | `default` | No | Project name in LangSmith UI |
| `LANGSMITH_ENDPOINT` | `https://api.smith.langchain.com` | No | LangSmith API endpoint |

`POSTHOG_*` is a separate, independent integration implemented in
`agent.py` (`_posthog_llm_callbacks`): when the key is set, a LangChain
callback reports model, tokens, cost and latency per generation. A missing or
too-old `posthog` SDK logs a warning and degrades to no telemetry rather than
failing the agent.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `POSTHOG_API_KEY` | - | No | Enables PostHog LLM observability. Unset = no telemetry is collected or sent |
| `POSTHOG_HOST` | `https://us.i.posthog.com` | No | PostHog ingestion host (use `https://eu.i.posthog.com` for the EU region, or your own reverse proxy) |

**See**: `docs/LANGSMITH_TRACING.md` for detailed setup guide

## Executor Configuration

### Core Settings

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `KUBENTLY_API_URL` | - | Yes | URL of the Kubently API server. The executor exits at startup if this, `CLUSTER_ID` or `KUBENTLY_TOKEN` is missing |
| `CLUSTER_ID` | - | Yes | Unique identifier for the cluster. Set via Helm `executor.clusterId`; the chart falls back to the release namespace when that is empty |
| `KUBENTLY_TOKEN` | - | Yes | Authentication token for the executor. Must match the value stored in Redis at `executor:token:{CLUSTER_ID}` on the API side |
| `LOG_LEVEL` | `INFO` | No | Logging level |
| `EXECUTOR_VERSION` | `unknown` | No | Version string reported in capability/heartbeat payloads |

### TLS

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `KUBENTLY_SSL_VERIFY` | `true` | No | Set to `false` to skip TLS verification when dialling the API (development only) |
| `KUBENTLY_CA_CERT` | - | No | Path to a CA bundle for an API server using a private/self-signed certificate. Verification stays on |

An `http://` API URL with verification still enabled logs a warning at startup;
it is intended for local development only.

### Capability Reporting

Off by default. When on, the executor advertises its command whitelist (and,
when cloud mode is enabled, its detected cloud identity) to the central API so
the agent knows what each cluster can do before sending commands. Reporting
failures are logged and ignored — the executor keeps serving commands.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `KUBENTLY_REPORT_CAPABILITIES` | `false` | No | Enable capability reporting. Set via Helm `executor.capabilities.enabled`. Forced on when cloud mode is enabled (see below) |
| `KUBENTLY_HEARTBEAT_INTERVAL` | `300` | No | Seconds between heartbeats that refresh the reported capabilities' TTL. Set via Helm `executor.capabilities.heartbeatInterval` |

### Whitelist Configuration

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `KUBENTLY_WHITELIST_CONFIG` | `/etc/kubently/whitelist.yaml` | No | Path to the whitelist YAML. The chart mounts the rendered ConfigMap at exactly this path |

The whitelist's other knobs are fields **inside** that YAML file, not
environment variables — `reloadIntervalSeconds` (default `30`), `timeoutSeconds`
and `maxArguments`. Under Helm they come from
`executor.security.commandWhitelist.reloadInterval`, `.timeoutSeconds` and
`.maxArguments`. There are no `WHITELIST_PATH`, `REFRESH_INTERVAL` or
`TIMEOUT_SECONDS` variables. (The chart also sets `KUBENTLY_WHITELIST_DB` on the
executor pod; no code reads it today.)

### Cloud Telemetry (optional, default off)

The executor can answer read-only cloud telemetry queries — CloudWatch Logs
Insights, CloudWatch metrics, EKS control-plane logs and CloudTrail on AWS;
Cloud Logging, Cloud Monitoring and GKE audit logs on GCP — using the ambient
identity its pod already holds (EKS Pod Identity, IRSA, or GKE Workload
Identity). **No cloud credentials are configured here**: the variables below
only say which identity to look for. The full onboarding guide, including the
exact minimal IAM policy, is `docs/CLOUD_TELEMETRY.md`.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `KUBENTLY_CLOUD_MODE` | `off` | No | `off` disables the feature; `auto` tries AWS then GCP; `aws` and `gcp` pin one provider. Set via Helm `executor.cloud.enabled` + `executor.cloud.provider` |
| `KUBENTLY_CLOUD_AWS_REGION` | - (auto-detected) | No | Overrides the AWS region otherwise detected from the pod environment. Set via Helm `executor.cloud.awsRegion` |
| `KUBENTLY_CLOUD_GCP_PROJECT` | - (auto-detected) | No | Overrides the GCP project otherwise detected from the pod environment. Set via Helm `executor.cloud.gcpProject` |
| `KUBENTLY_CLOUD_REFRESH_INTERVAL` | `3600` | No | Seconds between re-detections of identity and usable permissions, so IAM grants and revocations are picked up without a pod restart. Set via Helm `executor.cloud.refreshInterval` |

Behaviour worth knowing before you enable it:

- **Cloud mode implies capability reporting.** The agent discovers cloud access
  through the capability report, so when cloud mode is on the executor turns
  `KUBENTLY_REPORT_CAPABILITIES` on itself and logs that it did.
- **Missing SDKs degrade, they do not fail.** The cloud module needs the
  `cloud` extra (`boto3`, `google-cloud-logging`, `google-cloud-monitoring`,
  `google-auth`; already installed in the published executor image). If the
  import fails, the executor logs
  `KUBENTLY_CLOUD_MODE set but cloud module unavailable ...` and continues with
  cloud operations disabled — kubectl work is unaffected.
- **The allowlist is in code.** Only the operations enumerated in
  `kubently/modules/executor/cloud/operations.py` can run, independently of how
  broad the IAM role you grant happens to be.
- **The API side has its own switch.** See `KUBENTLY_CLOUD_TOOLS` under API
  Server Configuration.

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
| `HOSTNAME` | Kubernetes / the chart | Pod name. The API deployment sets it explicitly from `metadata.name` and the code uses it to identify the serving instance |

### Docker Compose

For local development with Docker Compose:

`deployment/docker-compose.yaml` already sets the non-secret variables; the
`.env` file next to it only needs the provider selection and its key (see
`deployment/.env.example`):

```env
# .env file example
LLM_PROVIDER=anthropic-claude
ANTHROPIC_API_KEY=sk-ant-...
```

Executor tokens are not environment variables on the API side — create them
through `POST /admin/agents/{cluster_id}/token` once the API is up.

## Configuration Precedence

1. Environment variables (highest priority)
2. ConfigMap values (Kubernetes)
3. Default values in code (lowest priority)

## Security Considerations

### Sensitive Variables

The following variables contain sensitive data and should be stored in Kubernetes Secrets or secure vaults:

- `API_KEYS`
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`
- `KUBENTLY_TOKEN` (the executor's copy of its cluster token)
- `REDIS_PASSWORD`
- `ARGOCD_TOKEN`
- `SLACK_WEBHOOK_URL` (anyone holding it can post to your channel)
- `LANGSMITH_API_KEY`, `POSTHOG_API_KEY`
- MCP bearer tokens referenced by `bearer_token_env` / `headers_env`
- `KUBENTLY_GITOPS_TOKEN` (scope it to the manifests repo only — see the GitOps PR Remediation section)

### Example Kubernetes Secret

The chart expects two separately named secrets rather than one combined one:
`kubently-api-keys` (key: `keys`) for client API keys, and
`kubently-llm-secrets` (keys: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`GOOGLE_API_KEY`, `LANGSMITH_API_KEY`) for provider credentials.

```bash
kubectl create secret generic kubently-api-keys -n kubently \
  --from-literal=keys="admin:$(openssl rand -hex 32)"

kubectl create secret generic kubently-llm-secrets -n kubently \
  --from-literal=ANTHROPIC_API_KEY="sk-ant-..."
```

## Environment-Specific Configurations

These examples assume running the API directly (`python -m kubently.main`),
where `API_HOST`/`API_PORT`/`DEBUG` are honoured. In containers the port is
fixed by the image command — see the container note under Core Settings.

### Development

```bash
export API_PORT=8080
export LOG_LEVEL=DEBUG
export DEBUG=true
export REDIS_HOST=localhost
export LLM_PROVIDER=anthropic-claude
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
3. **Authentication failures**: Verify `API_KEYS` is set on the API, and that the executor's `KUBENTLY_TOKEN` matches `executor:token:{CLUSTER_ID}` in Redis
4. **Wrong A2A URL in agent card**: Set `A2A_EXTERNAL_URL` correctly
5. **Agent fails with `Unsupported LLM_PROVIDER`**: `LLM_PROVIDER` is required and has no default — set it under `api.env`

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
