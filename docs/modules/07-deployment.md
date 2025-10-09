# Module: Deployment Configuration

## Overview

This module contains all deployment configurations for the Kubently system, including Docker images, Kubernetes manifests, and local development setup.

## Components to Deploy

1. **Redis** - State storage
2. **Kubently API** - Central service
3. **Kubently Agent** - Per-cluster agent

## Directory Structure

```text
kubently/deployment/
├── docker/
│   ├── api/
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   ├── agent/
│   │   ├── Dockerfile
│   │   └── requirements.txt
├── kubernetes/
│   ├── namespace.yaml
│   ├── redis/
│   │   ├── deployment.yaml
│   │   └── service.yaml
│   ├── api/
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   ├── ingress.yaml
│   │   └── configmap.yaml
│   ├── agent/
│   │   ├── serviceaccount.yaml
│   │   ├── rbac.yaml
│   │   └── deployment.yaml
├── helm/
│   └── kubently/
│       ├── Chart.yaml
│       ├── values.yaml
│       └── templates/
└── docker-compose.yaml
```

## Docker Configurations

### API Dockerfile

```dockerfile
# deployment/docker/api/Dockerfile
FROM python:3.13-slim

# Install dependencies
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY kubently/ ./kubently/

# Create non-root user
RUN useradd -m -u 1000 kubently && \
    chown -R kubently:kubently /app
USER kubently

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8080/health')" || exit 1

# Run application
EXPOSE 8080
CMD ["uvicorn", "kubently.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

### API Requirements

```text
# deployment/docker/api/requirements.txt
fastapi==0.104.1
uvicorn[standard]==0.24.0
redis[hiredis]==5.0.1
pydantic==2.5.0
pydantic-settings==2.1.0
python-multipart==0.0.6
```

### Agent Dockerfile

```dockerfile
# deployment/docker/agent/Dockerfile
FROM python:3.13-alpine

# Install kubectl
RUN apk add --no-cache curl && \
    curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" && \
    chmod +x kubectl && \
    mv kubectl /usr/local/bin/ && \
    kubectl version --client && \
    apk del curl

# Install Python dependencies
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent
COPY agent.py .

# Create non-root user
RUN adduser -D -u 1000 kubently
USER kubently

# Run agent
CMD ["python", "-u", "agent.py"]
```

### Agent Requirements

```text
# deployment/docker/agent/requirements.txt
requests==2.31.0
urllib3==2.1.0
```

## Kubernetes Manifests

### Namespace

```yaml
# deployment/kubernetes/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: kubently
  labels:
    app: kubently
    version: "1.0.0"
```

### Redis Deployment

```yaml
# deployment/kubernetes/redis/deployment.yaml
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
        ports:
        - containerPort: 6379
        resources:
          requests:
            memory: "256Mi"
            cpu: "100m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        volumeMounts:
        - name: data
          mountPath: /data
        livenessProbe:
          tcpSocket:
            port: 6379
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          exec:
            command:
            - redis-cli
            - ping
          initialDelaySeconds: 5
          periodSeconds: 5
      volumes:
      - name: data
        emptyDir: {}  # Use PersistentVolume in production
---
# deployment/kubernetes/redis/service.yaml
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
  type: ClusterIP
```

### API Deployment

```yaml
# deployment/kubernetes/api/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kubently-api
  namespace: kubently
spec:
  replicas: 2
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
        - name: REDIS_URL
          value: "redis://redis:6379"
        - name: API_KEYS
          valueFrom:
            secretKeyRef:
              name: kubently-api-keys
              key: keys
        - name: LOG_LEVEL
          value: "INFO"
        resources:
          requests:
            memory: "256Mi"
            cpu: "200m"
          limits:
            memory: "512Mi"
            cpu: "1000m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 10
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 5
---
# deployment/kubernetes/api/service.yaml
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
  type: ClusterIP
---
# deployment/kubernetes/api/ingress.yaml
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
  ingressClassName: nginx
  tls:
  - hosts:
    - api.kubently.com
    secretName: kubently-api-tls
  rules:
  - host: api.kubently.com
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

### Agent Deployment

```yaml
# deployment/kubernetes/agent/serviceaccount.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: kubently-agent
  namespace: kubently
---
# deployment/kubernetes/agent/rbac.yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kubently-agent-readonly
rules:
- apiGroups: [""]
  resources: ["pods", "services", "endpoints", "nodes", "namespaces"]
  verbs: ["get", "list", "watch"]
- apiGroups: ["apps"]
  resources: ["deployments", "replicasets", "daemonsets", "statefulsets"]
  verbs: ["get", "list", "watch"]
- apiGroups: ["batch"]
  resources: ["jobs", "cronjobs"]
  verbs: ["get", "list", "watch"]
- apiGroups: ["networking.k8s.io"]
  resources: ["ingresses"]
  verbs: ["get", "list", "watch"]
- apiGroups: [""]
  resources: ["pods/log", "events"]
  verbs: ["get", "list"]
- apiGroups: ["metrics.k8s.io"]
  resources: ["pods", "nodes"]
  verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: kubently-agent-readonly
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: kubently-agent-readonly
subjects:
- kind: ServiceAccount
  name: kubently-agent
  namespace: kubently
---
# deployment/kubernetes/agent/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kubently-agent
  namespace: kubently
spec:
  replicas: 1
  selector:
    matchLabels:
      app: kubently-agent
  template:
    metadata:
      labels:
        app: kubently-agent
    spec:
      serviceAccountName: kubently-agent
      containers:
      - name: agent
        image: kubently/agent:latest
        env:
        - name: KUBENTLY_API_URL
          value: "https://api.kubently.com"
        - name: CLUSTER_ID
          valueFrom:
            fieldRef:
              fieldPath: metadata.namespace  # Or use ConfigMap
        - name: KUBENTLY_TOKEN
          valueFrom:
            secretKeyRef:
              name: kubently-agent-token
              key: token
        - name: LOG_LEVEL
          value: "INFO"
        resources:
          requests:
            memory: "128Mi"
            cpu: "50m"
          limits:
            memory: "256Mi"
            cpu: "200m"
```

### Secrets Creation

```bash
#!/bin/bash
# deployment/scripts/create-secrets.sh

# Create API keys secret
kubectl create secret generic kubently-api-keys \
    --namespace kubently \
    --from-literal=keys="key1,key2,key3"

# Create agent token secret
kubectl create secret generic kubently-agent-token \
    --namespace kubently \
    --from-literal=token="$(openssl rand -hex 32)"
```

## Local Development (Docker Compose)

```yaml
# deployment/docker-compose.yaml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  api:
    build:
      context: .
      dockerfile: deployment/docker/api/Dockerfile
    ports:
      - "8080:8080"
    environment:
      - REDIS_URL=redis://redis:6379
      - API_KEYS=test-key-1,test-key-2
      - LOG_LEVEL=DEBUG
    depends_on:
      redis:
        condition: service_healthy
    volumes:
      - ./kubently:/app/kubently  # For development

  agent:
    build:
      context: .
      dockerfile: deployment/docker/agent/Dockerfile
    environment:
      - KUBENTLY_API_URL=http://api:8080
      - CLUSTER_ID=local-test
      - KUBENTLY_TOKEN=test-token
      - LOG_LEVEL=DEBUG
    depends_on:
      - api
    volumes:
      - ~/.kube:/home/kubently/.kube:ro  # Mount kubeconfig

volumes:
  redis-data:

networks:
  default:
    name: kubently-network
```

## Helm Chart

```yaml
# deployment/helm/kubently/Chart.yaml
apiVersion: v2
name: kubently
description: Interactive Kubernetes Debugging System
type: application
version: 1.0.0
appVersion: "1.0.0"

dependencies:
  - name: redis
    version: 17.0.0
    repository: https://charts.bitnami.com/bitnami
    condition: redis.enabled
```

```yaml
# deployment/helm/kubently/values.yaml
replicaCount: 2

image:
  repository: kubently/api
  pullPolicy: IfNotPresent
  tag: "latest"

agent:
  image:
    repository: kubently/agent
    tag: "latest"
  clusterIdPrefix: ""  # Will be suffixed with namespace

redis:
  enabled: true
  auth:
    enabled: false
  persistence:
    enabled: true
    size: 1Gi

service:
  type: ClusterIP
  port: 80

ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: api.kubently.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: kubently-tls
      hosts:
        - api.kubently.com

resources:
  api:
    requests:
      cpu: 200m
      memory: 256Mi
    limits:
      cpu: 1000m
      memory: 512Mi
  agent:
    requests:
      cpu: 50m
      memory: 128Mi
    limits:
      cpu: 200m
      memory: 256Mi

autoscaling:
  enabled: false
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 80

# API configuration
apiKeys:
  - "prod-key-1"
  - "prod-key-2"

# Agent configuration
agent:
  enabled: true
  token: ""  # Generated if not provided
```

## Deployment Scripts

### Deploy Script

```bash
#!/bin/bash
# deployment/scripts/deploy.sh

set -e

ENVIRONMENT=${1:-staging}
NAMESPACE=${2:-kubently}

echo "Deploying Kubently to $ENVIRONMENT..."

# Create namespace
kubectl apply -f deployment/kubernetes/namespace.yaml

# Deploy Redis
kubectl apply -f deployment/kubernetes/redis/

# Wait for Redis
kubectl wait --for=condition=ready pod -l app=redis -n $NAMESPACE --timeout=60s

# Deploy API
kubectl apply -f deployment/kubernetes/api/

# Wait for API
kubectl wait --for=condition=ready pod -l app=kubently-api -n $NAMESPACE --timeout=60s

# Deploy Agent
kubectl apply -f deployment/kubernetes/agent/

echo "Deployment complete!"
```

### Build Script

```bash
#!/bin/bash
# deployment/scripts/build.sh

VERSION=${1:-latest}

echo "Building Kubently images version $VERSION..."

# Build API
docker build -t kubently/api:$VERSION -f deployment/docker/api/Dockerfile .

# Build Agent
docker build -t kubently/agent:$VERSION -f deployment/docker/agent/Dockerfile .

echo "Build complete!"
```

## Testing Deployment

```bash
#!/bin/bash
# deployment/scripts/test-deployment.sh

# Test API health
curl -f http://localhost:8080/health || exit 1

# Test Redis connection
redis-cli ping || exit 1

# Test agent connectivity
kubectl logs -n kubently deployment/kubently-agent --tail=10

echo "All tests passed!"
```

## Production Checklist

- [ ] Use external Redis cluster (AWS ElastiCache, Redis Cloud)
- [ ] Configure PersistentVolume for Redis if self-hosted
- [ ] Set up TLS certificates (cert-manager)
- [ ] Configure resource limits appropriately
- [ ] Set up monitoring (Prometheus, Grafana)
- [ ] Configure log aggregation (Fluentd, Loki)
- [ ] Set up backup strategy for Redis
- [ ] Implement secret rotation
- [ ] Configure network policies
- [ ] Set up autoscaling

## Deliverables

1. Docker images for API and Agent
2. Kubernetes manifests for all components
3. Helm chart for easy deployment
4. Docker Compose for local development
5. Deployment and build scripts
6. Production readiness checklist

## Development Notes

- Use multi-stage Docker builds for smaller images
- Pin all dependency versions for reproducibility
- Always run as non-root user
- Include health checks in all deployments
- Use ConfigMaps for configuration
- Use Secrets for sensitive data
- Label all resources consistently
