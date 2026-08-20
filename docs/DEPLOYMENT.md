# Kubently Deployment Guide

## Table of Contents
- [Prerequisites](#prerequisites)
- [Deployment Options](#deployment-options)
- [Production Deployment](#production-deployment)
- [Executor Deployment](#executor-deployment)
- [Configuration Management](#configuration-management)
- [Security Hardening](#security-hardening)
- [Monitoring Setup](#monitoring-setup)
- [Troubleshooting](#troubleshooting)

## Prerequisites

### Required Components
- Kubernetes cluster v1.24+ with RBAC enabled
- Redis — the chart deploys `redis/redis-stack-server` as a StatefulSet
  (`redis.enabled: true`). Redis Stack is used because the default
  conversation-memory backend needs the RediSearch module; on a managed Redis
  without it, set `KUBENTLY_CHECKPOINTER_BACKEND=plain-redis`
- kubectl configured with cluster access
- An LLM API key (Anthropic, OpenAI, or Google)

Images are published to `ghcr.io/kubently/kubently` (API + A2A) and
`ghcr.io/kubently/kubently-executor`; you only need your own registry if you
build them yourself.

### Required Tools
- `kubectl` v1.24+
- `helm` v3.0+
- `openssl` for generating API keys and tokens
- `docker`/`podman` only if you build images from source

### Network Requirements
- API service requires external ingress (LoadBalancer or Ingress)
- Executors require outbound HTTPS to API endpoint
- Redis requires internal cluster connectivity
- No inbound connections to executors needed

## Deployment Options

Kubently ships **one Helm chart** (`deployment/helm/kubently`, published as
`kubently/kubently`). What it deploys is decided by three switches:

| Switch | Default | Deploys |
|--------|---------|---------|
| `api.enabled` | `true` | API server + A2A + MCP endpoint, ingress, prompt/runbook ConfigMaps, proactive CronJobs |
| `redis.enabled` | `true` | The Redis Stack StatefulSet the API stores sessions, tokens and incident history in |
| `executor.enabled` | `true` | The in-cluster executor, its ServiceAccount, read-only ClusterRole and command whitelist |

A **central install** is the chart with `executor.enabled=false`. An
**executor-only install** on a monitored cluster is the *same chart* with
`api.enabled=false` and `redis.enabled=false`. There is no separate executor
chart.

### Option 1: `kubently install` (fastest)

```bash
npm install -g @kubently/cli
kubently install
```

The CLI runs Helm for you, creates the secrets, registers the executor token
and port-forwards the API. `kubently install --help` lists the flags
(`--provider`, `--chart ./deployment/helm/kubently` to install from a
checkout, and so on).

### Option 2: Helm (recommended for production)

```bash
# From the published chart repository
helm repo add kubently https://kubently.github.io/kubently
helm repo update

helm install kubently kubently/kubently \
  --namespace kubently \
  --create-namespace \
  --values custom-values.yaml

# ...or from a checkout
helm install kubently ./deployment/helm/kubently \
  --namespace kubently --create-namespace --values custom-values.yaml
```

See [GETTING_STARTED.md](GETTING_STARTED.md) for the full walkthrough with
secrets, ingress and remote executors.

### Option 3: Raw manifests

There is no committed all-in-one manifest. Generate manifests from the chart
instead — this keeps them in step with the chart rather than drifting from it:

```bash
make helm-template   # writes generated-manifests/kubently-manifests.yaml
# or directly:
helm template kubently ./deployment/helm/kubently \
  --namespace kubently --values custom-values.yaml > kubently.yaml
```

## Production Deployment

### Step 1: Create Secrets

All secrets are created manually — the chart never generates credentials.

```bash
kubectl create namespace kubently

# Redis password (required; redis.auth.existingSecret defaults to this name)
kubectl create secret generic kubently-redis-password -n kubently \
  --from-literal=password="$(openssl rand -base64 32)"

# Client API keys. The 'keys' field holds one entry per line (or per comma);
# each entry is either "key" or "service:key". Clients send the KEY part only.
export ADMIN_KEY=$(openssl rand -hex 32)
kubectl create secret generic kubently-api-keys -n kubently \
  --from-literal=keys="admin:${ADMIN_KEY}"

# LLM provider credentials. The secret NAME is fixed in the chart; every key
# inside it is optional, so include only the provider you use.
kubectl create secret generic kubently-llm-secrets -n kubently \
  --from-literal=ANTHROPIC_API_KEY="sk-ant-..."
```

### Step 2: Write your values file

```yaml
# custom-values.yaml
api:
  replicaCount: 3
  existingSecret: "kubently-api-keys"
  env:
    # Required — the agent has no default provider.
    LLM_PROVIDER: "anthropic-claude"
    LOG_LEVEL: "INFO"
    A2A_EXTERNAL_URL: "https://kubently.example.com/a2a/"

redis:
  enabled: true
  auth:
    existingSecret: "kubently-redis-password"
  master:
    persistence:
      enabled: true
      size: 5Gi          # chart default is 2Gi

# Executors live on the monitored clusters, not here.
executor:
  enabled: false
```

Redis persistence matters: executor tokens, sessions, cluster state and
incident history all live there. With persistence off, every token registered
through the admin API disappears on pod restart.

### Step 3: Install and verify

```bash
helm install kubently kubently/kubently \
  --namespace kubently --values custom-values.yaml

kubectl get pods -n kubently
kubectl port-forward -n kubently svc/kubently-api 8080:8080 &

curl http://localhost:8080/healthz   # unauthenticated probe -> {"status":"ok"}
curl http://localhost:8080/health    # detailed: redis, modules, tls, version
```

### Step 4: Configure Ingress (optional)

Set `ingress.enabled: true` with your controller's `className`, hosts and TLS
secret. The chart follows the "bring your own certificate" pattern — see
[deployment/helm/kubently/examples/](../deployment/helm/kubently/examples/)
for cert-manager, cloud load balancer, manual certificate and self-signed
patterns.

## Executor Deployment

Executors run on each monitored cluster and dial **outbound** to the API, so
they need no inbound ingress of their own.

### Step 1: Register the token with the API

The API accepts an executor only if a matching token exists in Redis under
`executor:token:{cluster_id}`.

The chart writes that key for you in exactly one case: a **co-located** release
where `api.enabled` and `executor.enabled` are both true and the token came
from `executor.token`. The API Deployment's `sync-executor-tokens` init
container then seeds it before the API starts. A remote executor is a separate
release with no API pod, so nothing runs that init container — register the
token through the admin API first.

```bash
export CLUSTER_ID="production-cluster-1"

# Let the API generate the token...
curl -X POST https://kubently.example.com/admin/agents/${CLUSTER_ID}/token \
  -H "X-API-Key: ${ADMIN_KEY}"

# ...or supply your own (32-128 chars, letters/digits/hyphens/underscores)
curl -X POST https://kubently.example.com/admin/agents/${CLUSTER_ID}/token \
  -H "X-API-Key: ${ADMIN_KEY}" -H 'Content-Type: application/json' \
  -d '{"token": "token-from-your-secrets-manager"}'
```

The response carries `{"token": ..., "clusterId": ..., "createdAt": ...}`. A
409 means a token already exists for that cluster id — delete it first with
`DELETE /admin/agents/{cluster_id}/token`.

Related admin endpoints: `GET /admin/agents` lists registered clusters and
`GET /admin/agents/{cluster_id}/status` reports one cluster's state.

### Step 2: Deploy the executor to the target cluster

```bash
kubectl config use-context ${CLUSTER_ID}
kubectl create namespace kubently

kubectl create secret generic kubently-executor-token -n kubently \
  --from-literal=token="<token from step 1>"

helm install kubently-executor kubently/kubently \
  --namespace kubently \
  --set api.enabled=false \
  --set redis.enabled=false \
  --set executor.enabled=true \
  --set executor.clusterId="${CLUSTER_ID}" \
  --set executor.apiUrl="https://kubently.example.com" \
  --set executor.existingSecret=kubently-executor-token
```

Notes:

- **No `replicaCount`.** The executor Deployment hardcodes `replicas: 1`: an
  executor *is* a cluster identity, and a second replica would register a
  duplicate agent for the same `clusterId`.
- **RBAC** comes from the chart's built-in read-only ClusterRole (pods and
  `pods/log`, services, endpoints, configmaps, PVs/PVCs, nodes, namespaces,
  events, quotas, workloads, jobs, ingresses, networkpolicies, RBAC objects,
  storage classes, HPAs, PDBs — Secrets deliberately excluded). Set
  `executor.rbacRules` only to *replace* that list, e.g. to add CRDs.
- **Command whitelist** is a second boundary on top of RBAC:
  `executor.security.mode` defaults to `readOnly`. `fullAccess` additionally
  requires the top-level `fullAccessAcknowledged: true`.
- **Cluster id** falls back to the release namespace when `executor.clusterId`
  is left empty.
- **`executor.existingSecret` and `api.enabled` together**: the API's
  `sync-executor-tokens` init container follows `executor.existingSecret` /
  `executor.existingSecretKey`, falling back to the chart-created
  `<release>-executor-token` secret, so a co-located release can reference a
  pre-created secret. On an executor-only release (`api.enabled=false`, as
  above) there is no init container at all.
- **TLS**: `KUBENTLY_SSL_VERIFY` / `KUBENTLY_CA_CERT` cover private CAs — see
  [MULTI_CLUSTER_TLS.md](MULTI_CLUSTER_TLS.md).

### Step 3: Verify

```bash
kubectl logs -n kubently -l app.kubernetes.io/component=executor
# look for the SSE connection being established

curl https://kubently.example.com/admin/agents -H "X-API-Key: ${ADMIN_KEY}"
```

### Multi-Cluster Executor Deployment

Repeat the two steps per cluster — each needs its own `clusterId` and token:

```bash
#!/bin/bash
# deploy-executors.sh
CLUSTERS=("cluster-1" "cluster-2" "cluster-3")
API_URL="https://kubently.example.com"

for CLUSTER in "${CLUSTERS[@]}"; do
  echo "Deploying to $CLUSTER..."

  # 1. Register the cluster with the API and capture the generated token
  TOKEN=$(curl -sS -X POST "${API_URL}/admin/agents/${CLUSTER}/token" \
    -H "X-API-Key: ${ADMIN_KEY}" | jq -r '.token')

  # 2. Deploy the executor onto that cluster
  kubectl config use-context "$CLUSTER"
  kubectl create namespace kubently --dry-run=client -o yaml | kubectl apply -f -
  kubectl create secret generic kubently-executor-token -n kubently \
    --from-literal=token="$TOKEN" --dry-run=client -o yaml | kubectl apply -f -

  helm install kubently-executor kubently/kubently \
    --namespace kubently \
    --set api.enabled=false \
    --set redis.enabled=false \
    --set executor.enabled=true \
    --set executor.clusterId="$CLUSTER" \
    --set executor.apiUrl="$API_URL" \
    --set executor.existingSecret=kubently-executor-token
done
```

## Configuration Management

### Environment-Specific Configurations

Use the real value paths — `api.replicaCount` (not `api.replicas`) and
`redis.master.persistence` / `redis.master.resources` (not `redis.persistence`).
The chart has no `monitoring` block; the Prometheus integration is
`prometheus.url`, which points the agent's `query_prometheus` tool at a
Prometheus the executor can reach.

```yaml
# values-production.yaml
api:
  replicaCount: 3
  existingSecret: "kubently-api-keys"
  resources:
    requests:
      memory: 512Mi
      cpu: 500m
    limits:
      memory: 1Gi
      cpu: 2000m
  env:
    LLM_PROVIDER: "anthropic-claude"
    LOG_LEVEL: "INFO"

redis:
  auth:
    existingSecret: "kubently-redis-password"
  master:
    persistence:
      enabled: true
      size: 20Gi
    resources:
      requests:
        memory: 2Gi
        cpu: 1000m

executor:
  enabled: false

# Optional read-only telemetry the agent can query through each executor
prometheus:
  url: "http://prometheus-operated.monitoring.svc.cluster.local:9090"
loki:
  url: "http://loki.monitoring.svc.cluster.local:3100"
```

```yaml
# values-staging.yaml
api:
  replicaCount: 2
  existingSecret: "kubently-api-keys"
  resources:
    requests:
      memory: 256Mi
      cpu: 250m
  env:
    LLM_PROVIDER: "anthropic-claude"

redis:
  master:
    persistence:
      enabled: true
      size: 10Gi

executor:
  enabled: false
```

For the optional feature blocks — `runbooks`, `mcpServers`, `fleetReport`,
`scheduledChecks`, `verifyDeployment`, `changeCorrelation`, `gitRemediation`,
`executor.cloud` — the annotated defaults in
`deployment/helm/kubently/values.yaml` are the reference, and every variable
they set is listed in [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md).

### Secret Management

#### Using Sealed Secrets

```bash
# Install sealed-secrets controller
kubectl apply -f https://github.com/bitnami-labs/sealed-secrets/releases/download/v0.18.0/controller.yaml

# Create sealed secret
echo -n "your-secret-token" | kubectl create secret generic kubently-token \
  --dry-run=client \
  --from-file=token=/dev/stdin \
  -o yaml | kubeseal -o yaml > sealed-token.yaml

# Apply sealed secret
kubectl apply -f sealed-token.yaml
```

#### Using External Secrets Operator

```yaml
# external-secret.yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: kubently-secrets
  namespace: kubently
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    # Must match api.existingSecret, and the key inside must be named "keys"
    name: kubently-api-keys
  data:
  - secretKey: keys
    remoteRef:
      key: kubently/api-keys
```

Kubently expects several small, purpose-named secrets rather than one combined
one: `kubently-api-keys` (key `keys`), `kubently-redis-password` (key
`password`), `kubently-llm-secrets` (keys `ANTHROPIC_API_KEY` /
`OPENAI_API_KEY` / `GOOGLE_API_KEY` / `LANGSMITH_API_KEY`), and
`kubently-executor-token` (key `token`) on each monitored cluster. Executor
tokens are not consumed from a secret by the API — they are registered through
`POST /admin/agents/{cluster_id}/token`.

## Security Hardening

### Network Policies

```yaml
# network-policy.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: kubently-api
  namespace: kubently
spec:
  podSelector:
    matchLabels:
      app: kubently-api
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          name: ingress-nginx
    ports:
    - protocol: TCP
      port: 8080
  egress:
  - to:
    - podSelector:
        matchLabels:
          app: redis
    ports:
    - protocol: TCP
      port: 6379
  - to: # Allow DNS
    - namespaceSelector: {}
      podSelector:
        matchLabels:
          k8s-app: kube-dns
    ports:
    - protocol: UDP
      port: 53
```

### Pod Security Policies

```yaml
# pod-security-policy.yaml
apiVersion: policy/v1beta1
kind: PodSecurityPolicy
metadata:
  name: kubently
spec:
  privileged: false
  allowPrivilegeEscalation: false
  requiredDropCapabilities:
    - ALL
  volumes:
    - 'configMap'
    - 'emptyDir'
    - 'projected'
    - 'secret'
    - 'downwardAPI'
    - 'persistentVolumeClaim'
  runAsUser:
    rule: 'MustRunAsNonRoot'
  seLinux:
    rule: 'RunAsAny'
  fsGroup:
    rule: 'RunAsAny'
  readOnlyRootFilesystem: true
```

### TLS Configuration

The Helm chart follows the "user brings certificate" pattern. TLS certificates must be created separately and referenced in your values.

For detailed TLS setup instructions and examples, see:

📁 **[deployment/helm/kubently/examples/](../deployment/helm/kubently/examples/)**

Available patterns:
- **cert-manager with Let's Encrypt** - Automatic certificate management (recommended for production)
- **Manual/existing certificates** - Use certificates from enterprise CA or purchased certificates
- **Cloud provider load balancers** - AWS ALB/ACM, GCP GCLB, Azure App Gateway
- **Development self-signed** - For local testing only

Quick example for manual certificate:

```bash
# Create TLS secret from existing certificate files
kubectl create secret tls kubently-api-tls \
  --cert=tls.crt \
  --key=tls.key \
  -n kubently

# Reference in values.yaml:
# ingress:
#   enabled: true
#   tls:
#     - secretName: kubently-api-tls
#       hosts:
#         - api.kubently.example.com
```

## Monitoring Setup

### Prometheus Metrics

```yaml
# service-monitor.yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: kubently-api
  namespace: kubently
spec:
  selector:
    matchLabels:
      app: kubently-api
  endpoints:
  - port: metrics
    interval: 30s
```

### Grafana Dashboard

```json
{
  "dashboard": {
    "title": "Kubently Monitoring",
    "panels": [
      {
        "title": "Request Rate",
        "targets": [
          {
            "expr": "rate(kubently_api_requests_total[5m])"
          }
        ]
      },
      {
        "title": "Command Execution Time",
        "targets": [
          {
            "expr": "histogram_quantile(0.95, kubently_command_duration_seconds)"
          }
        ]
      },
      {
        "title": "Active Sessions",
        "targets": [
          {
            "expr": "kubently_active_sessions"
          }
        ]
      }
    ]
  }
}
```

### Alerting Rules

```yaml
# prometheus-rules.yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: kubently-alerts
  namespace: kubently
spec:
  groups:
  - name: kubently
    rules:
    - alert: HighErrorRate
      expr: rate(kubently_api_errors_total[5m]) > 0.1
      annotations:
        summary: High error rate detected
    - alert: SlowCommandExecution
      expr: kubently_command_duration_seconds > 1
      annotations:
        summary: Commands taking longer than 1 second
    - alert: ExecutorDown
      expr: up{job="kubently-executor"} == 0
      annotations:
        summary: Kubently executor is down
```

## Troubleshooting

### Common Issues

#### Executor Not Connecting

```bash
# Check executor logs
kubectl logs -n kubently -l app=kubently-executor --tail=50

# Verify token
kubectl get secret kubently-executor-token -n kubently -o yaml

# Test API connectivity
kubectl run test-curl --image=curlimages/curl --rm -it -- \
  curl -H "Authorization: Bearer $TOKEN" https://api.kubently.example.com/health
```

#### Commands Timing Out

```bash
# Check Redis connectivity
kubectl exec -n kubently deployment/kubently-api -- redis-cli -h redis ping

# Check queue depth
kubectl exec -n kubently deployment/kubently-api -- redis-cli -h redis llen queue:cluster-1

# Increase timeout
kubectl set env deployment/kubently-api KUBENTLY_COMMAND_TIMEOUT=30 -n kubently
```

#### Session Expiring Too Quickly

```bash
# Increase session TTL
kubectl set env deployment/kubently-api KUBENTLY_SESSION_TTL=600 -n kubently

# Check Redis memory
kubectl exec -n kubently deployment/redis -- redis-cli info memory
```

### Debug Mode

Enable debug logging:

```bash
# API debug mode
kubectl set env deployment/kubently-api LOG_LEVEL=DEBUG -n kubently

# Executor debug mode
kubectl set env deployment/kubently-executor LOG_LEVEL=DEBUG -n kubently
```

### Health Checks

```bash
# API health
curl https://api.kubently.example.com/health

# API probe endpoint (unauthenticated)
curl https://api.kubently.example.com/healthz

# Redis health (the chart deploys a StatefulSet named <release>-redis-master)
kubectl exec -n kubently kubently-redis-master-0 -- redis-cli ping

# Executor status
kubectl get pods -n kubently -l app.kubernetes.io/component=executor
```

## Backup and Recovery

### Redis Backup

```bash
# Manual backup
kubectl exec -n kubently deployment/redis -- redis-cli BGSAVE
kubectl cp kubently/redis-xxx:/data/dump.rdb ./redis-backup.rdb

# Automated backup with CronJob
kubectl apply -f - <<EOF
apiVersion: batch/v1
kind: CronJob
metadata:
  name: redis-backup
  namespace: kubently
spec:
  schedule: "0 2 * * *"
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: backup
            image: redis:7-alpine
            command:
            - sh
            - -c
            - |
              redis-cli -h redis BGSAVE &&
              sleep 10 &&
              cp /data/dump.rdb /backup/dump-\$(date +%Y%m%d).rdb
            volumeMounts:
            - name: backup
              mountPath: /backup
          volumes:
          - name: backup
            persistentVolumeClaim:
              claimName: redis-backup
          restartPolicy: OnFailure
EOF
```

### Disaster Recovery

```bash
# Restore Redis from backup
kubectl cp ./redis-backup.rdb kubently/redis-xxx:/data/dump.rdb
kubectl exec -n kubently deployment/redis -- redis-cli SHUTDOWN
kubectl rollout restart deployment/redis -n kubently
```

## Upgrade Procedures

### Rolling Update

```bash
# Prefer `helm upgrade` so values and manifests stay in step:
helm upgrade kubently kubently/kubently -n kubently \
  --values custom-values.yaml --set api.image.tag=v2.0.0

# Monitor rollout
kubectl rollout status deployment/kubently-api -n kubently

# Update executors (per cluster) — same chart, executor image tag
helm upgrade kubently-executor kubently/kubently -n kubently \
  --values executor-values.yaml --set executor.image.tag=v2.0.0
```

### Blue-Green Deployment

```bash
# Deploy new version to green environment
helm install kubently-green kubently/kubently \
  --namespace kubently-green \
  --values values-production.yaml \
  --set api.image.tag=v2.0.0

# Test green environment
curl https://api-green.kubently.example.com/health

# Switch traffic to green
kubectl patch ingress kubently-api -n kubently \
  -p '{"spec":{"rules":[{"host":"api.kubently.example.com","http":{"paths":[{"backend":{"service":{"name":"kubently-api-green"}}}]}}]}}'

# Remove blue environment
helm uninstall kubently-blue -n kubently-blue
```
