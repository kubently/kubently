# Quick Start Guide

Get Kubently running locally in 5 minutes. For production deployment with external access/TLS, see [GETTING_STARTED.md](GETTING_STARTED.md).

## Prerequisites

- Kubernetes cluster with `kubectl` access
- Helm 3.x installed
- Node.js 18+ (for CLI)
- LLM API key (Anthropic, OpenAI, or Google)

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

# Deploy (API + executor in same cluster)
helm install kubently ./deployment/helm/kubently -n kubently \
  --set executor.enabled=true \
  --set executor.clusterId=local \
  --set executor.apiUrl=http://kubently-api:8080 \
  --set executor.token="${EXECUTOR_TOKEN}"

# Wait for ready
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=kubently -n kubently --timeout=120s
```

## Configure CLI

```bash
# Port-forward API
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
