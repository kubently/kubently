# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Kubently (*Kubernetes + Agentically*) enables AI agents to troubleshoot Kubernetes clusters through the [A2A (Agent-to-Agent) protocol](https://a2a-protocol.org/latest/). It allows agents powered by LLMs (Google Gemini, OpenAI, Anthropic) to autonomously diagnose and resolve cluster issues through natural language collaboration.

## Common Commands

### Development

```bash
# Install dependencies
make install-all

# Run locally
make run-local      # API server
make run-a2a        # A2A server

# Testing
make test           # Run unit tests
make lint           # Run linters
```

### Deployment & Testing

```bash
# Deploy to Kind cluster (preferred method)
./deploy-test.sh              # Deploys + runs automated tests
RUN_TESTS=false ./deploy-test.sh  # Skip tests

# Test manually
bash test-a2a.sh              # Basic A2A protocol tests

# Comprehensive test suite with AI analysis
./test-automation/run_tests.sh test-and-analyze --api-key test-api-key
./test-automation/run_tests.sh test-and-analyze --scenario 14-service-port-mismatch
```

### Docker & Helm

```bash
# Build multi-architecture images
make docker-build  # Builds API image only (use manual commands below for both)

# Manual build with multiple tags (latest, branch, SHA)
COMMIT_SHA=$(git rev-parse --short HEAD)
BRANCH=$(git branch --show-current)

# Build API image (includes A2A server)
docker buildx build --platform linux/amd64 \
  -f deployment/docker/api/Dockerfile \
  -t ghcr.io/kubently/kubently:latest \
  -t ghcr.io/kubently/kubently:${BRANCH} \
  -t ghcr.io/kubently/kubently:sha-${COMMIT_SHA} \
  --push .

# Build Executor image (includes kubectl)
docker buildx build --platform linux/amd64 \
  -f deployment/docker/executor/Dockerfile \
  -t ghcr.io/kubently/kubently-executor:latest \
  -t ghcr.io/kubently/kubently-executor:${BRANCH} \
  -t ghcr.io/kubently/kubently-executor:sha-${COMMIT_SHA} \
  --push .

# Helm deployment (always use Helm, not kubectl directly)
helm install kubently ./deployment/helm/kubently -f deployment/helm/test-values.yaml --namespace kubently
helm upgrade kubently ./deployment/helm/kubently -f deployment/helm/test-values.yaml --namespace kubently

# Generate raw Kubernetes manifests from Helm (if needed)
make helm-template  # Generates to generated-manifests/kubently-manifests.yaml
```

### Git Operations (from global CLAUDE.md)

```bash
gcob <branch>    # Checkout branch (ensures latest from main)
gcmwm "message"  # Commit with auto-formatting (auto-prefixes with branch name)
```

## Architecture

### Black Box Module Design

The codebase follows a **black box** architecture pattern where modules expose minimal interfaces and hide implementation details:

```
kubently/
├── modules/
│   ├── auth/        # Authentication (executor tokens, API keys)
│   ├── session/     # Session management (Redis-backed)
│   ├── queue/       # Command queue (Redis pub/sub)
│   ├── config/      # Configuration loading
│   └── a2a/         # A2A protocol implementation
│       └── protocol_bindings/a2a_server/
│           ├── agent.py           # Core agent + tool implementations
│           └── agent_executor.py  # A2A protocol executor
```

**Key Principle**: Each module can be swapped out without affecting others. For example, `AuthModule` can be replaced with OAuth/JWT/external auth without changing API endpoints.

### Core Components

1. **API Server** (`kubently/main.py`)
   - FastAPI REST API
   - Thin orchestration layer - business logic is in modules
   - Admin endpoints: `/admin/agents/{cluster_id}/token` (create/delete executor tokens)
   - Debug endpoints: `/debug/clusters`, `/debug/session`, `/debug/execute`
   - Health: `/healthz` (unauthenticated, for K8s probes), `/health` (detailed)

2. **A2A Server** (`kubently/modules/a2a/protocol_bindings/a2a_server/`)
   - Mounted at `/a2a/` (trailing slash required!)
   - Uses LangGraph for workflow orchestration
   - Streams responses via Server-Sent Events (SSE)
   - `agent.py`: Contains all tool implementations (debug_resource, get_pod_logs, execute_kubectl)
   - **Tool Call Tracing**: All tools MUST call `interceptor.record_tool_call()` and `interceptor.record_tool_result()`

3. **Authentication**
   - **Executor tokens**: Stored in Redis as `executor:token:{cluster_id}` = token_value
   - **API keys**: From `API_KEYS` env var (format: `service:key,service:key` or just `key,key`)
   - **No backwards compatibility**: No environment variable fallbacks (EXECUTOR_TOKEN_*, AGENT_TOKEN_* removed)
   - **AuthModule.extract_first_api_key()**: Static utility for internal service-to-service calls

4. **Redis Key Patterns**
   - `executor:token:{cluster_id}` - Executor authentication tokens
   - `cluster:active:{cluster_id}` - Active cluster markers
   - `cluster:session:{cluster_id}` - Session IDs per cluster
   - `session:{session_id}:*` - Session data
   - `auth:audit` - Security event log

5. **Test Automation** (`test-automation/`)
   - 20+ Kubernetes scenarios with AI-powered analysis
   - Uses Google Gemini to analyze tool calls and effectiveness
   - Key files: `run_tests.sh`, `test_runner.py`, `analyzer.py`

### A2A Protocol Details

**Critical**: A2A is a formal protocol - see https://a2a-protocol.org/latest/

```bash
# Endpoint format (trailing slash required!)
POST http://localhost:8080/a2a/

# Message format
{
  "jsonrpc": "2.0",
  "method": "message/stream",
  "params": {
    "message": {
      "messageId": "msg-123",
      "role": "user",
      "parts": [{"partId": "part-1", "text": "Show crashing pods"}]
    }
  }
}
```

**Testing**: Use `docs/TEST_QUERIES.md` for exact curl commands - format is very specific!

## Critical Development Rules

1. **Deployment**: Always use `./deploy-test.sh` - handles secrets, configuration, and testing correctly
2. **Helm First**: Update `deployment/helm/test-values.yaml` instead of manual kubectl changes
3. **Tool Tracing**: All A2A tools must implement interceptor tracing (required for test automation)
4. **No Ad-Hoc Test Scripts**: Use existing test infrastructure (`test-a2a.sh`, curl, test-automation/). If you create test_*.py files, delete them after use
5. **Documentation**: Doc files use ALL_CAPS (e.g., `GETTING_STARTED.md`, not `getting-started.md`)
6. **Security**: No default credentials, no test-api-key defaults in production code
7. **Changelog**: Maintain a changelog of all changes

## Token Management Workflow

**Admin creates token → deploys to executor cluster:**

```bash
# 1. Admin creates token via API or CLI
curl -X POST http://api/admin/agents/prod-cluster/token -H "X-API-Key: admin-key"
# Returns: {"token": "abc123...", "clusterId": "prod-cluster"}

# 2. Create secret on executor cluster
kubectl create secret generic kubently-executor-token \
  --from-literal=token="abc123..." \
  --namespace kubently

# 3. Deploy executor with Helm
helm install kubently-executor ./deployment/helm/kubently \
  --set api.enabled=false \
  --set redis.enabled=false \
  --set executor.enabled=true \
  --set executor.clusterId=prod-cluster \
  --set executor.apiUrl=https://kubently.company.com
```

**Executors typically run on REMOTE clusters**, not the same cluster as the API.

## Secret Management Best Practices

**CRITICAL**: Never commit secrets to version control. Always create secrets manually and reference them via `existingSecret`.

### API Keys (Client Authentication)

**RECOMMENDED**: Create secret manually, then reference it in Helm values:

```bash
# Create API key secret (one key per line)
kubectl create secret generic kubently-api-keys \
  --from-literal=keys="$(cat <<EOF
your-api-key-here
another-key-if-needed
EOF
)" \
  --namespace kubently

# Reference in production-values.yaml
api:
  existingSecret: "kubently-api-keys"
```

**Why this approach?**
- Prevents Helm from overwriting your manually created secret
- Keys persist across Helm upgrades
- Follows Kubernetes best practices for secret management

**Format**: The secret must have a `keys` field containing newline-separated API keys (just the key values, no prefixes).

### Executor Tokens

**RECOMMENDED**: Create secret manually before deployment:

```bash
# Generate secure token
EXECUTOR_TOKEN=$(openssl rand -hex 32)

# Create secret
kubectl create secret generic kubently-executor-token \
  --from-literal=token="${EXECUTOR_TOKEN}" \
  --namespace kubently

# Reference in values.yaml
executor:
  existingSecret: "kubently-executor-token"
  clusterId: "prod-cluster"
```

### Redis Passwords

**REQUIRED**: Redis password secret must be created manually before deployment.

```bash
# Create Redis password secret
kubectl create secret generic kubently-redis-password \
  --from-literal=password="$(openssl rand -base64 32)" \
  --namespace kubently

# Chart automatically references this secret (default setting):
# redis.auth.existingSecret: "kubently-redis-password"

# To use a different secret name, override in values.yaml:
redis:
  auth:
    existingSecret: "my-custom-redis-secret"
```

**Why this approach?**
- No auto-generation complexity - secrets are explicit and predictable
- Consistent pattern across all secrets (API keys, executor tokens, Redis)
- Prevents Helm ownership conflicts

### Alternative Secret Management Patterns

**CI/CD Integration**: Use secret management tools in your deployment pipeline:

```bash
# Example with cloud provider secret managers
EXECUTOR_TOKEN=$(aws secretsmanager get-secret-value --secret-id kubently/executor-token --query SecretString --output text)

helm install kubently ./deployment/helm/kubently \
  --set executor.token="${EXECUTOR_TOKEN}"
```

**GitOps Workflows**: Use encrypted secret management:
- [Sealed Secrets](https://github.com/bitnami-labs/sealed-secrets) - Encrypt secrets for Git storage
- [External Secrets Operator](https://external-secrets.io/) - Sync from external secret managers

### Development/Testing Only

`test-values.yaml` contains hardcoded tokens **only for local testing**. Never use this pattern in production.

## Managing Multiple Executor Clusters

### Adding Executor Tokens via Admin API

If you need to manage multiple executor clusters beyond what's defined in Helm values, use the admin API.

**Option 1: Auto-generate token** (secure random 64-char hex):
```bash
curl -X POST http://localhost:8080/admin/agents/kind-kubently/token \
  -H "X-API-Key: your-admin-key"

# Response: {"token": "abc123...", "clusterId": "kind-kubently", "createdAt": "2025-..."}
```

**Option 2: Provide custom token** (from Vault, secrets manager, etc.):
```bash
# Token must be 32-128 characters, alphanumeric + hyphens/underscores only
curl -X POST http://localhost:8080/admin/agents/kind-kubently/token \
  -H "X-API-Key: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{"token": "my-vault-managed-token-abc123-def456"}'

# Response: {"token": "my-vault-managed-token-abc123-def456", "clusterId": "kind-kubently", "createdAt": "2025-..."}

# Deploy executor to the remote cluster with this token (use the token from response)
kubectl create secret generic kubently-executor-token \
  --from-literal=token="my-vault-managed-token-abc123-def456" \
  --namespace kubently \
  --context kind-kubently

helm install kubently-executor ./deployment/helm/kubently \
  --set api.enabled=false \
  --set redis.enabled=false \
  --set executor.enabled=true \
  --set executor.existingSecret=kubently-executor-token \
  --set executor.clusterId=kind-kubently \
  --set executor.apiUrl=https://kubently.company.com
```

**IMPORTANT**: Redis persistence is now properly configured to save to `/data` (PVC-backed storage). Executor tokens created via the admin API will persist across pod restarts and Helm upgrades.

**Fixed in**: Revision 14 (Oct 21, 2025) - Redis now correctly saves to persistent volume

**Before the fix**: Redis was saving to `/var/lib/redis-stack` (ephemeral), causing all tokens added via admin API to disappear on pod restart.

## Common Issues

1. **Port-forward lost**: `kubectl port-forward -n kubently svc/kubently-api 8080:8080 &`
2. **A2A 404**: Check trailing slash `/a2a/` not `/a2a`
3. **Tool calls not captured**: Verify `interceptor.record_tool_call()` in `agent.py`
4. **Helm release broken**: Check if using deprecated env vars (AGENT_TOKEN_KUBENTLY removed)
5. **API keys reset after helm upgrade**: Re-create `kubently-api-keys` secret and restart pods

## Key Files

- `kubently/main.py` - API server entry point (thin orchestration)
- `kubently/modules/a2a/protocol_bindings/a2a_server/agent.py` - Core agent + all tools
- `kubently/modules/auth/auth.py` - Authentication module
- `deployment/helm/test-values.yaml` - Default deployment config
- `docs/TEST_QUERIES.md` - A2A protocol examples
- `test-automation/run_tests.sh` - Comprehensive test runner

## Environment Variables

Required:
- `API_KEYS` - API keys (format: `service:key,key`)
- `GOOGLE_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` - LLM API key

Optional:
- `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` - Redis connection
- `LLM_PROVIDER` - Provider selection (anthropic-claude, openai, google-gemini)
- `A2A_EXTERNAL_URL` - External A2A endpoint for agent card
- `LANGSMITH_TRACING` - Enable production tracing (default: false)
- `LANGSMITH_API_KEY` - LangSmith API key (via secret)
- `LANGSMITH_PROJECT` - Project name in LangSmith UI

See `docs/ENVIRONMENT_VARIABLES.md` for complete reference.
See `docs/LANGSMITH_TRACING.md` for production observability setup.

## Testing Workflow

1. Make changes
2. `./deploy-test.sh` (deploys + runs tests)
3. `./test-automation/run_tests.sh test-and-analyze --api-key test-api-key` (comprehensive)
4. Review `test-results-*/analysis/report.md`
5. Fix issues based on AI recommendations

## Additional Documentation

- **User Guides**: `docs/QUICK_START.md`, `docs/GETTING_STARTED.md`
- **Architecture**: `docs/ARCHITECTURE.md`, `docs/SYSTEM_DESIGN.md`
- **Deployment**: `docs/DEPLOYMENT.md`, `docs/GKE_DEPLOYMENT_ISSUES.md`
- **A2A Protocol**: https://a2a-protocol.org/latest/
