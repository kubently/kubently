# Quick Start Guide

Get Kubently running locally in 5 minutes. For production deployment with external access/TLS, see [GETTING_STARTED.md](GETTING_STARTED.md).

## Prerequisites

- Kubernetes cluster with `kubectl` access
- Helm 3.x installed
- Node.js 18+ (for CLI)
- LLM API key (Anthropic, OpenAI, or Google)

The fastest path is `kubently install`, which does everything below for you
(see the README). The steps here are the manual equivalent, for when you want
to see or customise each piece.

## Install & Deploy

```bash
# Install CLI
npm i -g @kubently/cli

# Clone and navigate to repo
git clone https://github.com/kubently/kubently.git
cd kubently

# Create namespace
kubectl create namespace kubently

# Create Redis password secret
kubectl create secret generic kubently-redis-password -n kubently \
  --from-literal=password="$(openssl rand -base64 32)"

# Create LLM secret (replace with your key)
kubectl create secret generic kubently-llm-secrets -n kubently \
  --from-literal=ANTHROPIC_API_KEY="your-key-here"

# Generate admin API key and executor token
export ADMIN_KEY=$(openssl rand -hex 32)
export EXECUTOR_TOKEN=$(openssl rand -hex 32)

# Create API keys secret
kubectl create secret generic kubently-api-keys -n kubently \
  --from-literal=keys="admin:${ADMIN_KEY}"
echo $ADMIN_KEY > ~/kubently-admin-key.txt

# Deploy (API + executor in same cluster — one chart, all components on)
# LLM_PROVIDER is required: the agent has no default provider.
helm install kubently ./deployment/helm/kubently -n kubently \
  --set api.existingSecret=kubently-api-keys \
  --set api.env.LLM_PROVIDER=anthropic-claude \
  --set executor.enabled=true \
  --set executor.clusterId=local \
  --set executor.apiUrl=http://kubently-api:8080 \
  --set executor.token="${EXECUTOR_TOKEN}"

# Wait for ready
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=kubently -n kubently --timeout=120s
```

## Register the Executor Token

Helm puts the token in a Secret for the executor to *present*, but nothing in
the chart tells the API to *accept* it — the API validates against Redis key
`executor:token:{cluster_id}`, which only the admin API writes. Register it
once, and the executor's next reconnect attempt succeeds:

```bash
# Port-forward the API
kubectl port-forward -n kubently svc/kubently-api 8080:8080 &

# Teach the API about this executor's token
curl -X POST http://localhost:8080/admin/agents/local/token \
  -H "X-API-Key: $(cat ~/kubently-admin-key.txt)" \
  -H 'Content-Type: application/json' \
  -d "{\"token\": \"${EXECUTOR_TOKEN}\"}"
```

The endpoint returns 409 if a token already exists for that cluster id; delete
it first (`DELETE /admin/agents/local/token`) to replace it. Custom tokens must
be 32–128 characters of letters, digits, hyphens or underscores — the
`openssl rand -hex 32` value above qualifies.

## Configure CLI

```bash
# Port-forward API (skip if you already started one above)
kubectl port-forward -n kubently svc/kubently-api 8080:8080 &

# Set environment variables
export KUBENTLY_API_URL="http://localhost:8080"
export KUBENTLY_API_KEY=$(cat ~/kubently-admin-key.txt)

# Verify
kubently admin  # Should show "local" cluster
```

## Use

```bash
kubently debug

# Try: "List all pods in kubently namespace"
# Try: "Show me any crashing pods"
```

## Next Steps

- **Add remote clusters**: See [GETTING_STARTED.md - Step 5](GETTING_STARTED.md#step-5-register-and-deploy-executors)
- **Production setup**: See [GETTING_STARTED.md - Step 3](GETTING_STARTED.md#step-3-configure-external-access-optional) for external access/TLS
- **Troubleshooting**: See [GETTING_STARTED.md - Common Issues](GETTING_STARTED.md#common-issues)

## Clean Up

```bash
helm uninstall kubently -n kubently
kubectl delete namespace kubently
```
