# Kubernetes Deployment for Kubently

This directory contains Kubernetes manifests and scripts for deploying Kubently.

## Quick Start with Kind (Local Testing)

1. **Copy and configure environment file:**
   ```bash
   cp .env.example .env
   # Edit .env with your LLM provider API keys
   ```

2. **Deploy to Kind cluster:**
   ```bash
   make kind-deploy
   ```

   This will:
   - Create a Kind cluster
   - Build and load Docker image
   - Create ConfigMap from your .env file
   - Deploy Redis and API services
   - Set up port forwarding

3. **Test the A2A interface:**
   ```bash
   make test-a2a-local
   # In another terminal:
   curl -X POST http://localhost:8080/a2a/ \
     -H 'Content-Type: application/json' \
     -H 'x-api-key: test-api-key' \
     -d @docs/test-queries/simple-test.json
   ```

## Configuration

### Environment Variables

The deployment uses a ConfigMap generated from your `.env` file. Key configurations:

- **LLM Provider**: Set `LLM_PROVIDER` to `openai`, `anthropic`, or `azure_openai`
- **API Keys**: Configure the appropriate API key for your chosen provider
- **Redis**: Automatically configured for in-cluster Redis

### Secrets Management

For production deployments, sensitive keys should use Kubernetes secrets:

```bash
# Create LLM API key secrets
make k8s-secrets
```

## Deployment Options

### Local Development (Kind)

```bash
# Create and deploy
make kind-deploy

# View logs
make kind-logs

# Clean up
make kind-delete
```

### Production Kubernetes

```bash
# Deploy to cluster
make k8s-deploy

# Create secrets
make k8s-secrets

# Check status
make k8s-status

# View logs
make k8s-logs
```

## Directory Structure

- `api/` - API server deployment and service
  - `deployment.yaml` - Standard deployment
  - `deployment-with-env.yaml` - Deployment with ConfigMap support
  - `service.yaml` - Service definitions
- `redis/` - Redis deployment and service
- `scripts/` - Helper scripts
  - `generate-configmap-from-env.sh` - Create ConfigMap from .env
  - `create-llm-secrets.sh` - Interactive secret creation
- `kind-config.yaml` - Kind cluster configuration

## Accessing Services

After deployment, services are available at:

- **API**: `http://localhost:8080` (via port-forward or NodePort)
- **A2A**: `http://localhost:8000` (via port-forward or NodePort)
- **Redis**: `redis://localhost:6379` (for debugging)

## Troubleshooting

1. **Check pod status:**
   ```bash
   kubectl get pods -n kubently
   ```

2. **View logs:**
   ```bash
   kubectl logs -n kubently -l app=kubently-api
   ```

3. **Verify ConfigMap:**
   ```bash
   kubectl get configmap kubently-env-config -n kubently -o yaml
   ```

4. **Check secrets:**
   ```bash
   kubectl get secrets -n kubently
   ```