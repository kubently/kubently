# Quick Start Guide

Get Kubently running locally in 5 minutes. For production deployment with TLS/ingress, see [GETTING_STARTED.md](GETTING_STARTED.md).

## Prerequisites

- Kubernetes cluster with `kubectl` access
- Node.js 18+ (for CLI)
- LLM API key (Anthropic, OpenAI, or Google)

## Install & Deploy

```bash
# Install CLI
npm install -g @kubently/cli

# Clone and navigate to repo
git clone https://github.com/your-org/kubently.git
cd kubently

# Create namespace and secrets
kubectl create namespace kubently
cd secrets && bash generate-redis-password.sh && cd ..

# Create LLM secret (replace with your key)
kubectl create secret generic kubently-llm-secrets -n kubently \
  --from-literal=ANTHROPIC_API_KEY="your-key-here"

# Generate and save admin API key
export ADMIN_KEY=$(openssl rand -hex 32)
kubectl create secret generic kubently-api-keys -n kubently \
  --from-literal=keys="admin:${ADMIN_KEY}"
echo $ADMIN_KEY > ~/kubently-admin-key.txt

# Deploy (API + executor in same cluster)
helm install kubently ./deployment/helm/kubently -n kubently \
  -f deployment/helm/test-values.yaml \
  --set executor.enabled=true \
  --set executor.clusterId=local \
  --set executor.apiUrl=http://kubently-api:8080

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
- **Production setup**: See [GETTING_STARTED.md - Step 3](GETTING_STARTED.md#step-3-configure-ingress-and-tls) for TLS/ingress
- **Troubleshooting**: See [GETTING_STARTED.md - Common Issues](GETTING_STARTED.md#common-issues)

## Clean Up

```bash
helm uninstall kubently -n kubently
kubectl delete namespace kubently
```
