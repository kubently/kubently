# Getting Started with Kubently

This guide will help you get Kubently up and running in your environment. We'll deploy the central API server, expose it securely, and connect remote cluster executors.

## Overview

Kubently uses a central API server architecture:
- **Central API**: Deployed to one cluster, handles AI queries and coordinates with executors
- **Executors**: Lightweight agents deployed to each Kubernetes cluster you want to manage
- **CLI**: Command-line tool for administration and querying clusters

**One chart, three switches.** There is a single Helm chart —
`deployment/helm/kubently` in this repo, published as `kubently/kubently` —
and you choose what it deploys with `api.enabled`, `redis.enabled` and
`executor.enabled`. A "central API install" is that chart with
`executor.enabled=false`; an "executor-only install" is the *same* chart with
`api.enabled=false` and `redis.enabled=false`. There is no separate executor
chart.

Both forms are shown below. To install from the published repository instead
of a checkout:

```bash
helm repo add kubently https://kubently.github.io/kubently
helm repo update
# then substitute `kubently/kubently` for `./deployment/helm/kubently`
```

## Prerequisites

- Kubernetes cluster for the central API (v1.24+)
- `kubectl` configured with cluster access
- `helm` v3.0+ for deployment
- Node.js 18+ (for the CLI tool)
- An LLM API key (Anthropic Claude, OpenAI, or Google Gemini)

## Step 1: Install the CLI

The Kubently CLI is required for administration and querying:

```bash
# Install Node.js CLI globally
npm i -g @kubently/cli

# Or install from source
git clone https://github.com/kubently/kubently.git
cd kubently/kubently-cli/nodejs
npm install && npm run build && npm link

# Verify installation
kubently --version
```

## Step 2: Deploy the Central API

### 2.1 Create Secrets

First, create the required secrets for your deployment:

```bash
# Switch to the cluster where you want to deploy the API
kubectl config use-context your-api-cluster

# Create namespace
kubectl create namespace kubently

# Create Redis password secret (REQUIRED)
kubectl create secret generic kubently-redis-password -n kubently \
  --from-literal=password="$(openssl rand -base64 32)"

# Create LLM API key secret (choose one or more providers)
kubectl create secret generic kubently-llm-secrets -n kubently \
  --from-literal=ANTHROPIC_API_KEY="your-anthropic-key" \
  --from-literal=OPENAI_API_KEY="your-openai-key" \
  --from-literal=GOOGLE_API_KEY="your-google-key"

# Create initial API key for CLI access
# Format: service:key (e.g., "admin:your-key-here")
export ADMIN_KEY=$(openssl rand -hex 32)
kubectl create secret generic kubently-api-keys -n kubently \
  --from-literal=keys="admin:${ADMIN_KEY}"

# Save your admin key - you'll need it for CLI access
echo "Your admin API key: ${ADMIN_KEY}" > ~/kubently-admin-key.txt
chmod 600 ~/kubently-admin-key.txt
```

**Note**: All secrets must be created manually before deployment. To use a different secret name for Redis, update `redis.auth.existingSecret` in your values file.

### 2.2 Deploy with Helm

Create a `values.yaml` file for your deployment:

```yaml
# my-values.yaml
api:
  # Chart default is 3; the API scales horizontally via the connection registry
  replicaCount: 2
  image:
    repository: ghcr.io/kubently/kubently
    tag: "latest"

  # Name of the API-key secret you created above. The chart default is ""
  # (empty), in which case the deployment falls back to "<release>-api-keys" —
  # which happens to be "kubently-api-keys" for a release named "kubently".
  # Set it explicitly so the reference survives a differently named release,
  # and so a helm upgrade never replaces your manually created secret.
  existingSecret: "kubently-api-keys"

  env:
    # REQUIRED — the agent has no default provider and fails to start without
    # this. Must contain "anthropic"/"claude", "openai"/"azure", or
    # "google"/"gemini".
    LLM_PROVIDER: "anthropic-claude"
    LOG_LEVEL: "INFO"
    A2A_EXTERNAL_URL: "https://kubently.yourdomain.com/a2a/"  # Update after ingress setup

redis:
  enabled: true
  # Auth defaults to the kubently-redis-password secret (created in Step 2.1)
  master:
    persistence:
      enabled: true
      size: 5Gi          # Chart default is 2Gi
      storageClass: ""   # Uses cluster default

# Executor not deployed with the API (deploy separately to other clusters).
# The chart default is true, so an API-only install must set this explicitly.
executor:
  enabled: false
```

**Provider keys**: the chart reads `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`GOOGLE_API_KEY` and `LANGSMITH_API_KEY` from a secret whose name is fixed at
`kubently-llm-secrets` — the one created in Step 2.1. Every key is optional,
so only the provider you selected needs to be present.

Deploy with Helm:

```bash
helm install kubently ./deployment/helm/kubently \
  --namespace kubently \
  --values my-values.yaml
```

Verify deployment:

```bash
# Check all pods are running
kubectl get pods -n kubently

# Expected output:
# kubently-api-xxx         1/1     Running
# kubently-redis-master-0  1/1     Running

# Check API health
kubectl port-forward -n kubently svc/kubently-api 8080:8080 &
curl http://localhost:8080/health
# Returns {"status":"healthy", "redis":"connected", "modules":"initialized", ...}
# /healthz is the unauthenticated probe endpoint and returns {"status":"ok"}
```

## Step 3: Configure External Access (Optional)

For production deployments where remote executors need to connect from other clusters, you'll need to expose the Kubently API externally.

**For local/same-cluster testing**: Skip this step - executors can connect directly via ClusterIP service (`http://kubently-api:8080`).

**For production/cross-cluster**: Configure ingress with TLS. See detailed examples at:
📁 **[deployment/helm/kubently/examples/](../deployment/helm/kubently/examples/)**

Quick example using Helm values:

```yaml
# production-values.yaml
ingress:
  enabled: true
  className: nginx

  annotations:
    # Let cert-manager create the certificate automatically
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
    nginx.ingress.kubernetes.io/force-ssl-redirect: "true"

  hosts:
    - host: kubently.yourdomain.com
      paths:
        - path: /
          pathType: Prefix

  # cert-manager will create this secret automatically
  tls:
    - secretName: kubently-api-tls
      hosts:
        - kubently.yourdomain.com

# Secret references:
#   redis.auth.existingSecret defaults to "kubently-redis-password" — override
#     only if you named the secret differently.
#   api.existingSecret defaults to "" — set it (as in my-values.yaml above) so
#     the chart uses your manually created kubently-api-keys secret instead of
#     rendering one from api.apiKeys.
```

Deploy with ingress and production secrets:

```bash
helm install kubently ./deployment/helm/kubently \
  --namespace kubently \
  --values production-values.yaml
```

**Note**: You must have a ClusterIssuer configured (e.g., letsencrypt-prod). See [TLS examples](../deployment/helm/kubently/examples/) for setup instructions.

### Update A2A External URL

After setting up ingress, update the `A2A_EXTERNAL_URL` in your deployment:

```bash
# Update the helm values
helm upgrade kubently ./deployment/helm/kubently \
  --namespace kubently \
  --values my-values.yaml \
  --set api.env.A2A_EXTERNAL_URL="https://kubently.yourdomain.com/a2a/"

# Or patch the deployment directly
kubectl set env deployment/kubently-api -n kubently \
  A2A_EXTERNAL_URL="https://kubently.yourdomain.com/a2a/"
```

## Step 4: Configure the CLI

Set up the CLI to connect to your Kubently API:

```bash
# Initialize CLI configuration
kubently init

# You'll be prompted for:
# API URL: https://kubently.yourdomain.com
# API Key: <paste your admin key from Step 2.1>

# Or set via environment variables
export KUBENTLY_API_URL="https://kubently.yourdomain.com"
export KUBENTLY_API_KEY="your-admin-key-from-step-2.1"
```

Verify CLI connection:

```bash
# Should list clusters (none yet)
kubently admin

# Select "List Clusters"
# Should show empty list or clusters with tokens
```

## Step 5: Register and Deploy Executors

Now you'll connect other Kubernetes clusters by deploying executors to them.

### 5.1 Create Executor Token

Use the CLI to generate a token for each cluster you want to connect:

```bash
# Run admin menu
kubently admin

# Select "Add Cluster"
# Enter cluster ID: production-us-west
# Copy the generated token

# Or use direct API call:
curl -X POST https://kubently.yourdomain.com/admin/agents/production-us-west/token \
  -H "X-API-Key: your-admin-key" \
  | jq -r '.token'
```

**Important**: Save the token securely - you'll need it to deploy the executor.

### 5.2 Deploy Executor to Remote Cluster

Switch to the cluster you want to monitor:

```bash
# Switch to the remote cluster
kubectl config use-context production-us-west

# Create namespace
kubectl create namespace kubently

# Create executor token secret
kubectl create secret generic kubently-executor-token -n kubently \
  --from-literal=token="<paste-token-from-5.1>"

# Create executor values — same chart, API and Redis switched off
cat > executor-values.yaml <<EOF
# Disable API and Redis (executor only)
api:
  enabled: false

redis:
  enabled: false

# Enable executor
executor:
  enabled: true
  clusterId: "production-us-west"  # Must match the cluster ID used when creating the token
  apiUrl: "https://kubently.yourdomain.com"

  # Use the secret created above rather than putting the token in values
  existingSecret: "kubently-executor-token"
  existingSecretKey: "token"
EOF

# Deploy executor with Helm — the same chart as the API install
helm install kubently-executor ./deployment/helm/kubently \
  --namespace kubently \
  --values executor-values.yaml
```

Notes on the executor values, all verifiable in
`deployment/helm/kubently/values.yaml`:

- **No `replicaCount`.** Executors always run one replica: the deployment
  hardcodes `replicas: 1` because each executor *is* a cluster identity, and a
  second replica would register a duplicate agent for the same `clusterId`.
- **RBAC is `executor.rbacRules`, a flat list** — there is no
  `executor.rbac.create` / `executor.rbac.rules`. Leave it empty (the default)
  to get the chart's built-in read-only ClusterRole, which already covers pods,
  `pods/log`, services, endpoints, configmaps, PVs/PVCs, nodes, namespaces,
  events, quotas, deployments, daemonsets, statefulsets, replicasets, jobs,
  cronjobs, ingresses, networkpolicies, RBAC objects, storage classes, HPAs and
  PDBs — and deliberately excludes Secrets. Set `rbacRules` only to *replace*
  those defaults, e.g. to add CRDs.
- **Command whitelist**: `executor.security.mode` defaults to `readOnly`, which
  is a second boundary on top of RBAC. `fullAccess` additionally requires
  the top-level `fullAccessAcknowledged: true`.

### 5.3 Verify Executor Connection

Check that the executor connected successfully:

```bash
# Check executor pod logs
kubectl logs -n kubently -l app.kubernetes.io/component=executor

# Should see: "SSE connection established"

# List clusters from CLI (switch back to your local context)
kubently admin
# Select "List Clusters"
# Should see: production-us-west - ✓ Connected
```

## Step 6: Start Using Kubently

You're ready to query your clusters with AI assistance!

### Interactive Debug Session

```bash
# Start interactive session
kubently debug

# You'll be prompted to select a cluster
# Then ask questions like:
# - "What pods are crashing in the default namespace?"
# - "Show me pods using the most CPU"
# - "Are there any pending pods?"
```

### Query Specific Cluster

```bash
# Query a specific cluster directly
kubently debug --cluster production-us-west

# Ask: "What's the status of the ingress-nginx namespace?"
```

### Admin Operations

```bash
# List all registered clusters
kubently admin
# Select: List Clusters

# View cluster status
kubently admin
# Select: View Cluster Status
# Choose: production-us-west

# Remove a cluster
kubently admin
# Select: Remove Cluster
# Choose cluster to remove (this revokes the token)
```

## Adding More Clusters

To add additional clusters, repeat Step 5 for each cluster:

1. Generate a new token with `kubently admin` → "Add Cluster"
2. Deploy executor to that cluster with the token
3. Verify connection

Each cluster needs its own unique token and cluster ID.

## Common Issues

### Executor Not Connecting

**Symptoms**: Executor shows as "Disconnected" in admin panel

**Solutions**:
1. Check executor pod logs:
   ```bash
   kubectl logs -n kubently -l app.kubernetes.io/component=executor
   ```

2. Verify token is correct:
   ```bash
   # On executor cluster
   kubectl get secret kubently-executor-token -n kubently -o jsonpath='{.data.token}' | base64 -d

   # Compare to token in Redis on API cluster
   kubectl exec -n kubently kubently-redis-master-0 -- \
     redis-cli GET "executor:token:your-cluster-id"
   ```

3. Check network connectivity:
   ```bash
   # From executor pod, test API connectivity
   kubectl exec -n kubently -it deployment/kubently-executor -- \
     curl https://kubently.yourdomain.com/health
   ```

4. Verify cluster ID matches:
   ```bash
   # Check executor deployment
   kubectl get deployment kubently-executor -n kubently -o yaml | grep CLUSTER_ID
   ```

### API Key Authentication Failed

**Symptoms**: CLI returns "401 Unauthorized" or admin endpoints return "Invalid API key"

**Solutions**:
1. Check API key format in secret:
   ```bash
   kubectl get secret kubently-api-keys -n kubently -o jsonpath='{.data.keys}' | base64 -d
   # Should show: service:key (e.g., "admin:abc123...")
   ```

2. If secret shows "# No API keys configured" (can happen after helm upgrades):
   ```bash
   # Generate new admin key
   export ADMIN_KEY=$(openssl rand -hex 32)

   # Update the secret
   kubectl create secret generic kubently-api-keys -n kubently \
     --from-literal=keys="admin:${ADMIN_KEY}" \
     --dry-run=client -o yaml | kubectl apply -f -

   # Restart API pods to pick up new secret
   kubectl rollout restart deployment/kubently-api -n kubently

   # Save the new key
   echo "${ADMIN_KEY}" > ~/kubently-admin-key.txt
   chmod 600 ~/kubently-admin-key.txt
   ```

3. To add additional API keys (e.g., for different services):
   ```bash
   # Get existing keys
   EXISTING_KEYS=$(kubectl get secret kubently-api-keys -n kubently -o jsonpath='{.data.keys}' | base64 -d)

   # Generate new key for a service
   NEW_KEY=$(openssl rand -hex 32)

   # Add to existing keys (comma-separated)
   kubectl create secret generic kubently-api-keys -n kubently \
     --from-literal=keys="${EXISTING_KEYS},myservice:${NEW_KEY}" \
     --dry-run=client -o yaml | kubectl apply -f -

   # Restart API pods
   kubectl rollout restart deployment/kubently-api -n kubently
   ```

4. Verify CLI configuration:
   ```bash
   kubently init  # Reconfigure with new key
   ```

### Redis Data Lost After Restart

**Symptoms**: All executor tokens and clusters disappear after Redis restart

**Solutions**:
1. Verify Redis persistence is enabled:
   ```bash
   kubectl get pvc -n kubently
   # Should show: redis-data-kubently-redis-master-0
   ```

2. If PVC doesn't exist, update helm values to enable persistence:
   ```yaml
   redis:
     master:
       persistence:
         enabled: true
         size: 5Gi
   ```

3. Redeploy:
   ```bash
   helm upgrade kubently ./deployment/helm/kubently \
     --namespace kubently \
     --values my-values.yaml
   ```

## Next Steps

- **Review Architecture**: See [ARCHITECTURE.md](ARCHITECTURE.md) for system design
- **Configure Advanced Auth**: See [A2A_AUTHENTICATION.md](A2A_AUTHENTICATION.md) for OAuth/OIDC
- **Set Up Monitoring**: See [DEPLOYMENT.md](DEPLOYMENT.md#monitoring-setup) for observability
- **Cloud Telemetry**: See [CLOUD_TELEMETRY.md](CLOUD_TELEMETRY.md) to let executors answer CloudWatch/Cloud Logging questions via workload identity
- **Configuration Reference**: See [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md) for every variable the API and executor read

## Support

- **Documentation**: See `/docs` directory for comprehensive guides
- **Issues**: Report bugs or request features at [GitHub Issues](https://github.com/kubently/kubently/issues)
- **Community**: Join discussions at [GitHub Discussions](https://github.com/kubently/kubently/discussions)
