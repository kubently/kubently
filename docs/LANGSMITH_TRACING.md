# LangSmith Tracing for Kubently

This guide explains how to enable LangSmith tracing for production observability in your Kubently deployment on GKE.

## Overview

LangSmith provides automatic tracing for LangChain/LangGraph applications, capturing:
- **LLM interactions**: All prompts, responses, token usage, and latency
- **Tool calls**: Every kubectl execution, cluster queries, and diagnostics
- **Agent reasoning**: Full decision-making traces through the LangGraph workflow
- **Performance metrics**: End-to-end request timing and bottleneck identification
- **Error tracking**: Failed tool calls, timeouts, and LLM errors

## Prerequisites

1. **LangSmith Account**: Sign up at https://smith.langchain.com/
2. **API Key**: Generate from Settings > API Keys in LangSmith UI
3. **GKE Cluster**: Running Kubently deployment
4. **kubectl Access**: Cluster admin permissions

## Quick Start

### 1. Get Your LangSmith API Key

```bash
# Login to https://smith.langchain.com/
# Navigate to Settings > API Keys
# Create a new API key (copy it immediately - shown only once)
export LANGSMITH_API_KEY="lsv2_pt_..."
```

### 2. Create Kubernetes Secret

Add your LangSmith API key to the existing `kubently-llm-secrets` secret:

```bash
# Option A: Update existing secret (if it exists)
kubectl get secret kubently-llm-secrets -n kubently -o json | \
  jq --arg key "$LANGSMITH_API_KEY" '.data.LANGSMITH_API_KEY = ($key | @base64)' | \
  kubectl apply -f -

# Option B: Create new secret with all LLM keys
kubectl create secret generic kubently-llm-secrets \
  --from-literal=LANGSMITH_API_KEY="${LANGSMITH_API_KEY}" \
  --from-literal=ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
  --from-literal=GOOGLE_API_KEY="${GOOGLE_API_KEY}" \
  --from-literal=OPENAI_API_KEY="${OPENAI_API_KEY}" \
  --namespace kubently \
  --dry-run=client -o yaml | kubectl apply -f -
```

### 3. Configure Helm Values

Update your production values file (e.g., `production-values.yaml`):

```yaml
api:
  env:
    # Enable LangSmith tracing
    LANGSMITH_TRACING: "true"
    LANGSMITH_PROJECT: "kubently-production"  # Change to your project name
    LANGSMITH_ENDPOINT: "https://api.smith.langchain.com"
```

### 4. Rebuild Docker Image

The updated dependencies must be built into your image:

```bash
# Build with LangSmith support
COMMIT_SHA=$(git rev-parse --short HEAD)
BRANCH=$(git branch --show-current)

docker buildx build --platform linux/amd64 \
  -f deployment/docker/api/Dockerfile \
  -t ghcr.io/kubently/kubently:latest \
  -t ghcr.io/kubently/kubently:${BRANCH} \
  -t ghcr.io/kubently/kubently:sha-${COMMIT_SHA} \
  --push .
```

### 5. Deploy to GKE

```bash
# Upgrade your Helm release
helm upgrade kubently ./deployment/helm/kubently \
  -f production-values.yaml \
  --namespace kubently

# Restart pods to pick up new image and secrets
kubectl rollout restart deployment/kubently-api -n kubently

# Verify tracing is enabled
kubectl logs -n kubently -l app.kubernetes.io/component=api --tail=50 | grep -i langsmith
```

## Verification

### Check Tracing Configuration

```bash
# Verify environment variables are set
kubectl exec -n kubently deployment/kubently-api -- env | grep LANGSMITH
# Expected output:
# LANGSMITH_TRACING=true
# LANGSMITH_PROJECT=kubently-production
# LANGSMITH_ENDPOINT=https://api.smith.langchain.com
# LANGSMITH_API_KEY=lsv2_pt_...
```

### View Traces in LangSmith

1. Go to https://smith.langchain.com/
2. Navigate to **Projects** > Select your project (e.g., `kubently-production`)
3. Trigger a test query to your Kubently instance:
   ```bash
   # Port-forward if needed
   kubectl port-forward -n kubently svc/kubently-api 8080:8080 &

   # Send test A2A query (see docs/TEST_QUERIES.md for format)
   curl -X POST http://localhost:8080/a2a/ \
     -H "Content-Type: application/json" \
     -H "X-API-Key: your-api-key" \
     -d '{
       "jsonrpc": "2.0",
       "method": "message/stream",
       "params": {
         "message": {
           "messageId": "test-123",
           "role": "user",
           "parts": [{"partId": "1", "text": "Show me pods in default namespace"}]
         }
       }
     }'
   ```

4. Return to LangSmith UI - you should see a new trace appear with:
   - Full conversation flow
   - LLM prompts and responses
   - Tool calls (execute_kubectl, list_clusters)
   - Timing breakdown

## Configuration Options

### Environment Variables

All LangSmith configuration is controlled via environment variables in `api.env`:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LANGSMITH_TRACING` | No | `false` | Enable/disable tracing |
| `LANGSMITH_API_KEY` | Yes* | - | API key from LangSmith (set via secret) |
| `LANGSMITH_PROJECT` | No | `default` | Project name in LangSmith UI |
| `LANGSMITH_ENDPOINT` | No | `https://api.smith.langchain.com` | LangSmith API endpoint |

*Required when `LANGSMITH_TRACING=true`

### Project Organization

Use different project names for different environments:

```yaml
# Development
LANGSMITH_PROJECT: "kubently-dev"

# Staging
LANGSMITH_PROJECT: "kubently-staging"

# Production
LANGSMITH_PROJECT: "kubently-production"
```

### Sampling (Optional)

To reduce trace volume in high-traffic environments, use sampling:

```yaml
api:
  env:
    LANGSMITH_TRACING: "true"
    LANGSMITH_SAMPLE_RATE: "0.1"  # Trace 10% of requests
```

## Troubleshooting

### Traces Not Appearing

1. **Check API key is set correctly**:
   ```bash
   kubectl get secret kubently-llm-secrets -n kubently -o jsonpath='{.data.LANGSMITH_API_KEY}' | base64 -d
   ```

2. **Verify environment variables**:
   ```bash
   kubectl exec deployment/kubently-api -n kubently -- env | grep LANGSMITH
   ```

3. **Check pod logs for errors**:
   ```bash
   kubectl logs -n kubently -l app.kubernetes.io/component=api --tail=100 | grep -i error
   ```

4. **Ensure image has langsmith package**:
   ```bash
   kubectl exec deployment/kubently-api -n kubently -- pip list | grep langsmith
   # Should show: langsmith X.X.X
   ```

### High Latency After Enabling

LangSmith traces are sent asynchronously, but network issues can cause delays:

1. **Check LangSmith endpoint connectivity**:
   ```bash
   kubectl exec deployment/kubently-api -n kubently -- \
     curl -I https://api.smith.langchain.com
   ```

2. **Temporarily disable to verify**:
   ```yaml
   LANGSMITH_TRACING: "false"
   ```

3. **Use sampling to reduce volume**:
   ```yaml
   LANGSMITH_SAMPLE_RATE: "0.1"
   ```

### Secret Update Not Applied

Kubernetes doesn't automatically reload secrets:

```bash
# Force pod restart after secret update
kubectl rollout restart deployment/kubently-api -n kubently

# Verify new secret is mounted
kubectl exec deployment/kubently-api -n kubently -- \
  env | grep LANGSMITH_API_KEY
```

## Production Best Practices

### 1. Secret Management

**Never commit API keys to version control**. Use one of these approaches:

```bash
# Option A: External secret manager (recommended)
# Use Google Secret Manager, AWS Secrets Manager, or HashiCorp Vault
gcloud secrets create kubently-langsmith-key --data-file=- <<< "$LANGSMITH_API_KEY"

# Option B: CI/CD injection
# Store in GitHub Secrets, GitLab CI Variables, etc.
# Inject during deployment pipeline
```

### 2. Project Isolation

Use separate LangSmith projects per environment to avoid mixing traces:

```yaml
# dev-values.yaml
LANGSMITH_PROJECT: "kubently-dev"

# staging-values.yaml
LANGSMITH_PROJECT: "kubently-staging"

# production-values.yaml
LANGSMITH_PROJECT: "kubently-production"
```

### 3. Cost Management

LangSmith has usage limits on free tier. For production:

- **Enable sampling**: `LANGSMITH_SAMPLE_RATE: "0.1"` (10% of requests)
- **Monitor usage**: Check LangSmith dashboard for trace counts
- **Upgrade plan**: Consider LangSmith Plus/Enterprise for high-volume production

### 4. Privacy & Compliance

LangSmith traces contain user queries and Kubernetes resource data. Ensure compliance:

- Review LangSmith's data retention policies
- Consider self-hosted LangSmith for sensitive environments
- Use LangSmith's PII redaction features if needed
- Document what data is sent to LangSmith in your privacy policy

## Advanced Usage

### Custom Metadata

Add custom metadata to traces for better filtering:

```python
# In kubently/modules/a2a/protocol_bindings/a2a_server/agent.py
import langsmith

# Add metadata to specific runs
with langsmith.trace(
    name="kubernetes_diagnosis",
    metadata={
        "cluster_id": cluster_id,
        "user": user_id,
        "namespace": namespace
    }
) as run:
    # Your agent logic here
    pass
```

### Filtering Traces

In LangSmith UI, filter by:
- **Time range**: Last hour, day, week
- **Status**: Success, error, timeout
- **Duration**: >5s, >10s
- **Metadata**: Custom fields you added
- **Tags**: Custom tags for categorization

### Exporting Traces

Export traces for analysis or backup:

```bash
# Using LangSmith CLI
pip install langsmith
langsmith export --project kubently-production --output traces.jsonl
```

## Comparing with Existing Tools

### LangSmith vs. Test Automation

Your existing `test-automation/` and `langsmith-experiments/` directories serve different purposes:

| Feature | test-automation/ | langsmith-experiments/ | LangSmith Tracing (This Guide) |
|---------|-----------------|------------------------|-------------------------------|
| **Purpose** | Automated testing | Offline evaluation | Production observability |
| **When** | Pre-deployment | Development/testing | Runtime (production) |
| **Data Source** | Test scenarios | LangSmith datasets | Live user queries |
| **Output** | Pass/fail reports | Evaluation metrics | Real-time traces |
| **Use Case** | CI/CD validation | Prompt optimization | Debugging, monitoring |

### Migration Path

You can gradually migrate from test-automation to LangSmith:

1. **Phase 1**: Enable production tracing (this guide)
2. **Phase 2**: Convert test scenarios to LangSmith datasets (already done!)
3. **Phase 3**: Run experiments via `langsmith-experiments/`
4. **Phase 4**: Replace bash-based test-automation with LangSmith evaluations

## Resources

- **LangSmith Docs**: https://docs.smith.langchain.com/
- **LangChain Tracing Guide**: https://python.langchain.com/docs/langsmith/
- **Your Experiments**: `langsmith-experiments/README.md`
- **Test Queries**: `docs/TEST_QUERIES.md`

## Next Steps

After enabling tracing:

1. **Monitor traces** in LangSmith UI to understand agent behavior
2. **Identify bottlenecks** - slow tool calls, inefficient prompts
3. **Debug failures** - see exact sequence of events leading to errors
4. **Optimize prompts** - test variations in `langsmith-experiments/`
5. **Set up alerts** - LangSmith can notify you of error spikes

---

**Questions?** Check existing issues or create a new one at https://github.com/kubently/kubently/issues
