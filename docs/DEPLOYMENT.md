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
- Redis 7.0+ (can be deployed in-cluster or external)
- kubectl configured with cluster access
- Docker registry for container images

### Required Tools
- `kubectl` v1.24+
- `helm` v3.0+ (optional, for Helm deployment)
- `docker` or `podman` for building images
- `openssl` for generating tokens

### Network Requirements
- API service requires external ingress (LoadBalancer or Ingress)
- Executors require outbound HTTPS to API endpoint
- Redis requires internal cluster connectivity
- No inbound connections to executors needed

## Deployment Options

### Option 1: Quick Start (Development)

```bash
# Deploy everything with default settings
kubectl apply -f https://raw.githubusercontent.com/kubently/kubently/main/deploy/quickstart.yaml

# This includes:
# - Namespace creation
# - Redis deployment
# - API deployment
# - Basic executor deployment
```

### Option 2: Helm Chart (Recommended)

```bash
# Add Helm repository
helm repo add kubently https://kubently.github.io/kubently
helm repo update

# Install with custom values
helm install kubently kubently/kubently \
  --namespace kubently \
  --create-namespace \
  --values custom-values.yaml
```

### Option 3: Manual Deployment (Production)

Follow the detailed steps in the [Production Deployment](#production-deployment) section.

## Production Deployment

### Step 1: Create Namespace and Secrets

```bash
# Create namespace
kubectl create namespace kubently

# Generate API keys for clients
export API_KEY_1=$(openssl rand -hex 32)
export API_KEY_2=$(openssl rand -hex 32)

# Create API keys secret
kubectl create secret generic kubently-api-keys \
  --from-literal=keys="${API_KEY_1},${API_KEY_2}" \
  -n kubently

# Generate executor tokens
export CLUSTER_1_TOKEN=$(openssl rand -hex 32)
export CLUSTER_2_TOKEN=$(openssl rand -hex 32)

# Create executor tokens secret
kubectl create secret generic kubently-executor-tokens \
  --from-literal=tokens='{"cluster-1":"'${CLUSTER_1_TOKEN}'","cluster-2":"'${CLUSTER_2_TOKEN}'"}' \
  -n kubently
```

### Step 2: Deploy Redis

#### Option A: In-Cluster Redis

```yaml
# redis-deployment.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: redis-config
  namespace: kubently
data:
  redis.conf: |
    maxmemory 2gb
    maxmemory-policy allkeys-lru
    save 900 1
    save 300 10
    save 60 10000
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: redis-data
  namespace: kubently
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 10Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis
  namespace: kubently
spec:
  replicas: 1
  selector:
    matchLabels:
      app: redis
  template:
    metadata:
      labels:
        app: redis
    spec:
      containers:
      - name: redis
        image: redis:7-alpine
        command:
          - redis-server
          - /etc/redis/redis.conf
        ports:
        - containerPort: 6379
        volumeMounts:
        - name: config
          mountPath: /etc/redis
        - name: data
          mountPath: /data
        resources:
          requests:
            memory: "1Gi"
            cpu: "500m"
          limits:
            memory: "2Gi"
            cpu: "1000m"
      volumes:
      - name: config
        configMap:
          name: redis-config
      - name: data
        persistentVolumeClaim:
          claimName: redis-data
---
apiVersion: v1
kind: Service
metadata:
  name: redis
  namespace: kubently
spec:
  selector:
    app: redis
  ports:
  - port: 6379
    targetPort: 6379
```

#### Option B: External Redis

```yaml
# external-redis-secret.yaml
apiVersion: v1
kind: Secret
metadata:
  name: redis-connection
  namespace: kubently
type: Opaque
data:
  url: cmVkaXM6Ly91c2VyOnBhc3N3b3JkQHJlZGlzLmV4YW1wbGUuY29tOjYzNzk= # base64 encoded
```

### Step 3: Deploy Kubently API

```yaml
# api-deployment.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: kubently-config
  namespace: kubently
data:
  config.yaml: |
    redis_url: redis://redis:6379
    api_port: 8080
    api_workers: 4
    command_timeout: 10
    session_ttl: 300
    result_ttl: 60
    max_commands_per_fetch: 10
    long_poll_timeout: 30
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kubently-api
  namespace: kubently
spec:
  replicas: 3
  selector:
    matchLabels:
      app: kubently-api
  template:
    metadata:
      labels:
        app: kubently-api
    spec:
      containers:
      - name: api
        image: kubently/api:latest
        ports:
        - containerPort: 8080
        env:
        - name: KUBENTLY_REDIS_URL
          value: redis://redis:6379
        - name: KUBENTLY_API_KEYS
          valueFrom:
            secretKeyRef:
              name: kubently-api-keys
              key: keys
        - name: KUBENTLY_AGENT_TOKENS
          valueFrom:
            secretKeyRef:
              name: kubently-executor-tokens
              key: tokens
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 10
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 10
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "1000m"
---
apiVersion: v1
kind: Service
metadata:
  name: kubently-api
  namespace: kubently
spec:
  selector:
    app: kubently-api
  ports:
  - port: 80
    targetPort: 8080
  type: LoadBalancer
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: kubently-api
  namespace: kubently
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: kubently-api
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
```

### Step 4: Configure Ingress (Optional)

```yaml
# ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: kubently-api
  namespace: kubently
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/proxy-body-size: "10m"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "60"
spec:
  tls:
  - hosts:
    - api.kubently.example.com
    secretName: kubently-tls
  rules:
  - host: api.kubently.example.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: kubently-api
            port:
              number: 80
```

## Executor Deployment

### Deploy Executor to Target Cluster

```bash
# 1. Generate token for this cluster
export CLUSTER_ID="production-cluster-1"
export TOKEN=$(openssl rand -hex 32)
echo "Cluster: $CLUSTER_ID"
echo "Token: $TOKEN"

# 2. Save token on API side (add to executor tokens)
kubectl patch secret kubently-executor-tokens -n kubently \
  --type='json' -p='[{"op":"add","path":"/data/tokens","value":"..."}]'

# 3. Create executor namespace in target cluster
kubectl create namespace kubently

# 4. Create token secret
kubectl create secret generic kubently-executor-token \
  --from-literal=token=$TOKEN \
  -n kubently

# 5. Apply executor deployment
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: kubently-executor
  namespace: kubently
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kubently-executor-readonly
rules:
- apiGroups: ["*"]
  resources: ["*"]
  verbs: ["get", "list", "watch"]
- nonResourceURLs: ["*"]
  verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: kubently-executor-readonly
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: kubently-executor-readonly
subjects:
- kind: ServiceAccount
  name: kubently-executor
  namespace: kubently
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kubently-executor
  namespace: kubently
spec:
  replicas: 1
  selector:
    matchLabels:
      app: kubently-executor
  template:
    metadata:
      labels:
        app: kubently-executor
    spec:
      serviceAccountName: kubently-executor
      containers:
      - name: executor
        image: kubently/executor:latest
        env:
        - name: KUBENTLY_API_URL
          value: "https://api.kubently.example.com"
        - name: CLUSTER_ID
          value: "${CLUSTER_ID}"
        - name: KUBENTLY_TOKEN
          valueFrom:
            secretKeyRef:
              name: kubently-executor-token
              key: token
        - name: LOG_LEVEL
          value: "INFO"
        resources:
          requests:
            memory: "64Mi"
            cpu: "50m"
          limits:
            memory: "256Mi"
            cpu: "200m"
EOF
```

### Multi-Cluster Executor Deployment

For deploying executors across multiple clusters, use this script:

```bash
#!/bin/bash
# deploy-executors.sh

CLUSTERS=("cluster-1" "cluster-2" "cluster-3")
API_URL="https://api.kubently.example.com"

for CLUSTER in "${CLUSTERS[@]}"; do
  echo "Deploying to $CLUSTER..."

  # Generate token
  TOKEN=$(openssl rand -hex 32)

  # Switch context
  kubectl config use-context $CLUSTER

  # Deploy executor
  helm install kubently-executor kubently/executor \
    --namespace kubently \
    --create-namespace \
    --set cluster.id=$CLUSTER \
    --set cluster.token=$TOKEN \
    --set api.url=$API_URL

  echo "Executor deployed to $CLUSTER with token: $TOKEN"
done
```

## Configuration Management

### Environment-Specific Configurations

```yaml
# values-production.yaml
api:
  replicas: 3
  resources:
    requests:
      memory: 512Mi
      cpu: 500m
    limits:
      memory: 1Gi
      cpu: 2000m

redis:
  persistence:
    enabled: true
    size: 20Gi
  resources:
    requests:
      memory: 2Gi
      cpu: 1000m

monitoring:
  enabled: true
  prometheus:
    enabled: true
  grafana:
    enabled: true
```

```yaml
# values-staging.yaml
api:
  replicas: 2
  resources:
    requests:
      memory: 256Mi
      cpu: 250m

redis:
  persistence:
    enabled: true
    size: 10Gi

monitoring:
  enabled: false
```

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
    name: kubently-secrets
  data:
  - secretKey: api-keys
    remoteRef:
      key: kubently/api-keys
  - secretKey: executor-tokens
    remoteRef:
      key: kubently/executor-tokens
```

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
#         - api.kubently.com
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

# Redis health
kubectl exec -n kubently deployment/redis -- redis-cli ping

# Executor status
kubectl get pods -n kubently -l app=kubently-executor
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
# Update API
kubectl set image deployment/kubently-api api=kubently/api:v2.0.0 -n kubently

# Monitor rollout
kubectl rollout status deployment/kubently-api -n kubently

# Update executors (per cluster)
kubectl set image deployment/kubently-executor executor=kubently/executor:v2.0.0 -n kubently
```

### Blue-Green Deployment

```bash
# Deploy new version to green environment
helm install kubently-green kubently/kubently \
  --namespace kubently-green \
  --values values-production.yaml \
  --set version=v2.0.0

# Test green environment
curl https://api-green.kubently.example.com/health

# Switch traffic to green
kubectl patch ingress kubently-api -n kubently \
  -p '{"spec":{"rules":[{"host":"api.kubently.example.com","http":{"paths":[{"backend":{"service":{"name":"kubently-api-green"}}}]}}]}}'

# Remove blue environment
helm uninstall kubently-blue -n kubently-blue
```
