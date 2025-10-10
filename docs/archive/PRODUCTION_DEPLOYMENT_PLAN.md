# Kubently Production Deployment Plan

## Overview

This plan outlines the deployment of Kubently to a GKE cluster with public DNS (`test-api.kubently.io`) and TLS certificates from Let's Encrypt. This will be the first production-grade test of Kubently with:

- **GKE Cluster**: Production Kubernetes environment
- **Public DNS**: `test-api.kubently.io` (Hostinger)
- **TLS Certificates**: Let's Encrypt (automatic renewal via cert-manager)
- **Multi-cluster Setup**: GKE as API server, local Kind cluster as managed cluster
- **CLI Testing**: Full end-to-end testing with kubently CLI

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         Internet                             │
└────────────────────────┬────────────────────────────────────┘
                         │
                         │ DNS: test-api.kubently.io
                         │ TLS: Let's Encrypt
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              GKE Cluster (Production)                        │
│  ┌────────────────────────────────────────────────────┐     │
│  │  Ingress (nginx) + cert-manager                    │     │
│  │  - Automatic TLS certificate provisioning          │     │
│  │  - HTTPS termination                               │     │
│  └─────────────────────┬──────────────────────────────┘     │
│                        │                                     │
│  ┌─────────────────────▼──────────────────────────────┐     │
│  │  Kubently API Server (namespace: kubently)         │     │
│  │  - A2A protocol at /a2a/                           │     │
│  │  - Executor registration                           │     │
│  │  - Redis for state management                      │     │
│  └────────────────────────────────────────────────────┘     │
│                                                              │
│  ┌────────────────────────────────────────────────────┐     │
│  │  Kubently Executor (GKE cluster ID: "gke")         │     │
│  │  - Manages GKE cluster resources                   │     │
│  └────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────┘
                         ▲
                         │ HTTPS API calls
                         │
┌─────────────────────────────────────────────────────────────┐
│        Kind Cluster (Local - kind-kubently)                  │
│  ┌────────────────────────────────────────────────────┐     │
│  │  Kubently Executor (namespace: kubently-remote)    │     │
│  │  - Cluster ID: "kind-remote"                       │     │
│  │  - API URL: https://test-api.kubently.io           │     │
│  │  - Manages local Kind cluster resources            │     │
│  └────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────┘
                         ▲
                         │
                    ┌────▼────┐
                    │   CLI   │
                    │  Client │
                    └─────────┘
```

## Prerequisites

### 1. GKE Cluster
- [x] GKE cluster exists: `kubently-test-cluster1` in `us-central1`
- [x] Cluster is accessible: `gke_regal-skyline-471806-t6_us-central1_kubently-test-cluster1`
- [ ] cert-manager installed
- [ ] nginx-ingress-controller installed

### 2. DNS Configuration
- [x] Domain ownership: `kubently.io` (Hostinger)
- [ ] DNS A record: `test-api.kubently.io` → GKE Ingress IP

### 3. Local Environment
- [x] Kind cluster: `kind-kubently`
- [x] Docker images available locally
- [x] Helm charts ready

### 4. TLS Certificate Setup
- [ ] Let's Encrypt ClusterIssuer configured
- [ ] DNS validation for Let's Encrypt

## Implementation Steps

### Phase 1: GKE Infrastructure Setup

#### Step 1.1: Install cert-manager
```bash
# Switch to GKE cluster
kubectl config use-context gke_regal-skyline-471806-t6_us-central1_kubently-test-cluster1

# Install cert-manager
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.16.2/cert-manager.yaml

# Wait for cert-manager to be ready
kubectl wait --for=condition=Available --timeout=300s \
  deployment/cert-manager -n cert-manager
kubectl wait --for=condition=Available --timeout=300s \
  deployment/cert-manager-webhook -n cert-manager
kubectl wait --for=condition=Available --timeout=300s \
  deployment/cert-manager-cainjector -n cert-manager
```

#### Step 1.2: Install nginx-ingress-controller
```bash
# Install nginx ingress controller
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update

helm install nginx-ingress ingress-nginx/ingress-nginx \
  --namespace ingress-nginx \
  --create-namespace \
  --set controller.service.type=LoadBalancer

# Wait for LoadBalancer to get external IP
kubectl wait --for=jsonpath='{.status.loadBalancer.ingress}' \
  --timeout=300s service/nginx-ingress-ingress-nginx-controller \
  -n ingress-nginx

# Get the external IP
INGRESS_IP=$(kubectl get service nginx-ingress-ingress-nginx-controller \
  -n ingress-nginx -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
echo "Ingress IP: $INGRESS_IP"
```

#### Step 1.3: Configure Let's Encrypt ClusterIssuer
```bash
# Create Let's Encrypt staging issuer (for testing)
cat <<EOF | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-staging
spec:
  acme:
    server: https://acme-staging-v02.api.letsencrypt.org/directory
    email: adam@dickinson.works
    privateKeySecretRef:
      name: letsencrypt-staging
    solvers:
    - http01:
        ingress:
          class: nginx
EOF

# Create Let's Encrypt production issuer
cat <<EOF | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: adam@dickinson.works
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
    - http01:
        ingress:
          class: nginx
EOF
```

#### Step 1.4: DNS Configuration (Manual)
**Action Required:** Update DNS in Hostinger:
1. Log into Hostinger DNS management for `kubently.io`
2. Create A record:
   - Name: `test-api`
   - Type: A
   - Value: `<INGRESS_IP from Step 1.2>`
   - TTL: 300 (5 minutes)
3. Wait for DNS propagation (5-15 minutes)
4. Verify: `dig test-api.kubently.io +short` should return the ingress IP

### Phase 2: Deploy Kubently to GKE

#### Step 2.1: Create production values file
```bash
# Create GKE-specific Helm values
cat > /Users/adickinson/repos/kubently/deployment/helm/gke-production-values.yaml <<'EOF'
# GKE Production Values for Kubently

# TLS Configuration - External mode with Let's Encrypt
tls:
  enabled: true
  mode: "external"
  external:
    domain: "test-api.kubently.io"
    issuer: "letsencrypt-prod"  # Use letsencrypt-staging for testing first
    duration: "2160h"  # 90 days
    renewBefore: "720h"  # Renew 30 days before expiry

# API Configuration
api:
  replicaCount: 2

  image:
    repository: kubently/api
    tag: latest
    pullPolicy: IfNotPresent

  service:
    type: ClusterIP
    port: 8080
    targetPort: 8080

  # API Keys for authentication
  apiKeys:
    - "test-api-key"
    - "gke-executor-token-$(openssl rand -hex 32)"

  env:
    LOG_LEVEL: "INFO"
    MAX_COMMANDS_PER_FETCH: "10"
    COMMAND_TIMEOUT: "30"
    SESSION_TTL: "300"
    PORT: "8080"
    API_PORT: "8080"
    A2A_ENABLED: "true"
    A2A_EXTERNAL_URL: "https://test-api.kubently.io/a2a/"
    LLM_PROVIDER: "anthropic-claude"
    ANTHROPIC_MODEL_NAME: "claude-sonnet-4-20250514"
    AGENT_TOKEN_KIND: "test-api-key"
    KUBENTLY_API_URL: "http://kubently-api:8080"
    A2A_SERVER_DEBUG: "true"

  resources:
    requests:
      cpu: 200m
      memory: 512Mi
    limits:
      cpu: 1000m
      memory: 2Gi

# Executor for GKE cluster
executor:
  enabled: true
  replicaCount: 1

  image:
    repository: kubently/executor
    tag: latest
    pullPolicy: IfNotPresent

  clusterId: "gke"
  token: "gke-executor-token"  # Will be generated

  env:
    LOG_LEVEL: "INFO"

  resources:
    requests:
      cpu: 50m
      memory: 128Mi
    limits:
      cpu: 200m
      memory: 256Mi

# Security mode
kubentlyExecutor:
  enabled: true
  securityMode: "readOnly"
  commandWhitelist:
    enabled: true

# Redis
redis:
  enabled: true
  architecture: standalone
  auth:
    enabled: false
  master:
    persistence:
      enabled: true
      size: 2Gi

# Ingress - ENABLED for production
ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/force-ssl-redirect: "true"
    nginx.ingress.kubernetes.io/ssl-protocols: "TLSv1.2 TLSv1.3"
  hosts:
    - host: test-api.kubently.io
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: kubently-tls
      hosts:
        - test-api.kubently.io

# Service Account
serviceAccount:
  create: true
  name: kubently-executor

# Pod Security
podSecurityContext:
  runAsNonRoot: true
  runAsUser: 1000
  fsGroup: 1000

securityContext:
  capabilities:
    drop:
    - ALL
  readOnlyRootFilesystem: false
  runAsNonRoot: true
  runAsUser: 1000
EOF
```

#### Step 2.2: Build and push Docker images to GKE
```bash
# Switch to repo directory
cd /Users/adickinson/repos/kubently

# Build images
make docker-build

# Tag for GKE (if using private registry, otherwise skip)
# For testing, we'll use local images loaded to nodes

# For GKE, you may need to push to a registry:
# Option 1: Use Google Container Registry (GCR)
# docker tag kubently/api:latest gcr.io/regal-skyline-471806-t6/kubently/api:latest
# docker tag kubently/executor:latest gcr.io/regal-skyline-471806-t6/kubently/executor:latest
# docker push gcr.io/regal-skyline-471806-t6/kubently/api:latest
# docker push gcr.io/regal-skyline-471806-t6/kubently/executor:latest

# Option 2: Use Docker Hub (public)
# docker tag kubently/api:latest adickinson/kubently-api:latest
# docker tag kubently/executor:latest adickinson/kubently-executor:latest
# docker push adickinson/kubently-api:latest
# docker push adickinson/kubently-executor:latest
```

#### Step 2.3: Deploy Kubently to GKE
```bash
# Switch to GKE context
kubectl config use-context gke_regal-skyline-471806-t6_us-central1_kubently-test-cluster1

# Create namespace
kubectl create namespace kubently

# Create secrets for LLM API keys
kubectl create secret generic kubently-llm-secrets \
  -n kubently \
  --from-literal=ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
  --from-literal=GOOGLE_API_KEY="${GOOGLE_API_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -

# Deploy using Helm
helm upgrade --install kubently ./deployment/helm/kubently \
  --namespace kubently \
  --values deployment/helm/gke-production-values.yaml \
  --create-namespace \
  --wait

# Check deployment
kubectl get pods -n kubently
kubectl get ingress -n kubently
kubectl get certificate -n kubently
```

#### Step 2.4: Verify TLS Certificate
```bash
# Wait for certificate to be issued (may take 2-5 minutes)
kubectl wait --for=condition=Ready certificate/kubently-api-tls \
  -n kubently --timeout=300s

# Check certificate status
kubectl describe certificate kubently-api-tls -n kubently

# Test HTTPS endpoint
curl -I https://test-api.kubently.io/health
```

### Phase 3: Configure Local Kind Cluster as Remote Executor

#### Step 3.1: Create remote executor values
```bash
# Generate a unique token for the kind executor
KIND_EXECUTOR_TOKEN=$(openssl rand -hex 32)

# Create values file for Kind cluster executor
cat > /Users/adickinson/repos/kubently/deployment/helm/kind-remote-executor-values.yaml <<EOF
# Kind Cluster Remote Executor Values

# Disable API server (executor only)
api:
  replicaCount: 0

# Executor pointing to GKE API server
executor:
  enabled: true
  replicaCount: 1

  image:
    repository: kubently/executor
    tag: latest
    pullPolicy: IfNotPresent

  # Unique cluster ID
  clusterId: "kind-remote"

  # Token for authentication
  token: "${KIND_EXECUTOR_TOKEN}"

  # Point to production API server
  apiUrl: "https://test-api.kubently.io"

  env:
    LOG_LEVEL: "INFO"
    KUBENTLY_SSL_VERIFY: "true"  # Verify Let's Encrypt cert

  resources:
    requests:
      cpu: 50m
      memory: 128Mi
    limits:
      cpu: 200m
      memory: 256Mi

# Security mode
kubentlyExecutor:
  enabled: true
  securityMode: "readOnly"
  commandWhitelist:
    enabled: true

# Disable Redis (using GKE's Redis)
redis:
  enabled: false

# Disable ingress
ingress:
  enabled: false

# Service Account
serviceAccount:
  create: true
  name: kubently-executor

# TLS disabled (executor doesn't need it)
tls:
  enabled: false
  mode: "none"
EOF

# Save the token for registration
echo "KIND_EXECUTOR_TOKEN=${KIND_EXECUTOR_TOKEN}" >> /Users/adickinson/repos/kubently/.env.kind-remote
```

#### Step 3.2: Register Kind executor token with GKE API
```bash
# Switch to GKE context
kubectl config use-context gke_regal-skyline-471806-t6_us-central1_kubently-test-cluster1

# Add the kind executor token to API keys
kubectl get configmap kubently-api-agent-tokens -n kubently -o json | \
  jq --arg token "$KIND_EXECUTOR_TOKEN" '.data."kind-remote" = $token' | \
  kubectl apply -f -

# Or manually add to the configmap
kubectl edit configmap kubently-api-agent-tokens -n kubently
# Add: kind-remote: "<KIND_EXECUTOR_TOKEN>"

# Restart API pods to pick up new token
kubectl rollout restart deployment kubently-api -n kubently
```

#### Step 3.3: Deploy executor to Kind cluster
```bash
# Switch to Kind context
kubectl config use-context kind-kubently

# Create namespace
kubectl create namespace kubently-remote

# Deploy executor only
helm upgrade --install kubently-remote ./deployment/helm/kubently \
  --namespace kubently-remote \
  --values deployment/helm/kind-remote-executor-values.yaml \
  --create-namespace \
  --wait

# Check deployment
kubectl get pods -n kubently-remote
kubectl logs -n kubently-remote -l app=kubently-executor --tail=50
```

### Phase 4: Testing and Validation

#### Step 4.1: Test API Endpoints
```bash
# Test health endpoint
curl https://test-api.kubently.io/health

# Test A2A endpoint (should require authentication)
curl https://test-api.kubently.io/a2a/

# Test with authentication
curl -H "Authorization: Bearer test-api-key" \
  https://test-api.kubently.io/clusters
```

#### Step 4.2: Test CLI with both clusters
```bash
# Install/update CLI
cd /Users/adickinson/repos/kubently/kubently-cli/nodejs
npm install && npm run build && npm link

# Test CLI with GKE cluster
kubently debug \
  --api-url https://test-api.kubently.io \
  --api-key test-api-key \
  --cluster gke

# Test CLI with Kind cluster (remote)
kubently debug \
  --api-url https://test-api.kubently.io \
  --api-key test-api-key \
  --cluster kind-remote

# Interactive debugging session
kubently debug \
  --api-url https://test-api.kubently.io \
  --api-key test-api-key
# Should allow selecting between 'gke' and 'kind-remote' clusters
```

#### Step 4.3: End-to-End Test Scenarios
```bash
# Create test deployment in Kind cluster
kubectl config use-context kind-kubently
kubectl create namespace test-app
kubectl create deployment nginx --image=nginx:latest -n test-app

# Ask Kubently to debug the Kind cluster
kubently debug \
  --api-url https://test-api.kubently.io \
  --api-key test-api-key \
  --cluster kind-remote
# Query: "What pods are running in the test-app namespace?"

# Create test deployment in GKE cluster
kubectl config use-context gke_regal-skyline-471806-t6_us-central1_kubently-test-cluster1
kubectl create namespace test-app
kubectl create deployment nginx --image=nginx:latest -n test-app

# Ask Kubently to debug the GKE cluster
kubently debug \
  --api-url https://test-api.kubently.io \
  --api-key test-api-key \
  --cluster gke
# Query: "What pods are running in the test-app namespace?"
```

#### Step 4.4: TLS Certificate Verification
```bash
# Check certificate details
echo | openssl s_client -showcerts -servername test-api.kubently.io \
  -connect test-api.kubently.io:443 2>/dev/null | \
  openssl x509 -inform pem -noout -text | grep -E "Issuer:|Subject:|Not"

# Should show Let's Encrypt as issuer
```

### Phase 5: Monitoring and Troubleshooting

#### Step 5.1: Monitor logs
```bash
# GKE API server logs
kubectl logs -n kubently -l app=kubently-api --tail=100 -f

# GKE executor logs
kubectl logs -n kubently -l app=kubently-executor --tail=100 -f

# Kind executor logs
kubectl config use-context kind-kubently
kubectl logs -n kubently-remote -l app=kubently-executor --tail=100 -f

# Ingress controller logs
kubectl config use-context gke_regal-skyline-471806-t6_us-central1_kubently-test-cluster1
kubectl logs -n ingress-nginx -l app.kubernetes.io/name=ingress-nginx --tail=100
```

#### Step 5.2: Common issues and fixes
```bash
# Issue: Certificate not ready
kubectl describe certificate kubently-api-tls -n kubently
kubectl describe certificaterequest -n kubently
kubectl logs -n cert-manager -l app=cert-manager

# Issue: DNS not resolving
dig test-api.kubently.io +short
nslookup test-api.kubently.io

# Issue: Executor not connecting
kubectl logs -n kubently-remote -l app=kubently-executor
# Check KUBENTLY_API_URL and token in executor pod

# Issue: 502 Bad Gateway
kubectl get pods -n kubently
kubectl describe pods -n kubently
# Check if API pods are running and healthy
```

## Rollback Plan

### Emergency Rollback
```bash
# Rollback GKE deployment
kubectl config use-context gke_regal-skyline-471806-t6_us-central1_kubently-test-cluster1
helm rollback kubently -n kubently

# Rollback Kind executor
kubectl config use-context kind-kubently
helm rollback kubently-remote -n kubently-remote

# Complete removal
helm uninstall kubently -n kubently
helm uninstall kubently-remote -n kubently-remote
kubectl delete namespace kubently
kubectl delete namespace kubently-remote
```

## Success Criteria

- [ ] GKE cluster has cert-manager and nginx-ingress installed
- [ ] DNS record `test-api.kubently.io` points to ingress IP
- [ ] Let's Encrypt certificate is issued and valid
- [ ] HTTPS endpoint `https://test-api.kubently.io` is accessible
- [ ] GKE executor is registered and healthy
- [ ] Kind executor is registered and connects via HTTPS
- [ ] CLI can connect to API server over HTTPS
- [ ] CLI can query both GKE and Kind clusters
- [ ] A2A protocol works over HTTPS
- [ ] Certificate auto-renewal is configured

## Post-Deployment Tasks

1. **Documentation Updates**
   - [ ] Update README with production deployment instructions
   - [ ] Document DNS configuration steps
   - [ ] Add troubleshooting guide for TLS issues

2. **Security Hardening**
   - [ ] Review API key rotation policy
   - [ ] Implement rate limiting on ingress
   - [ ] Add monitoring for certificate expiry
   - [ ] Configure security headers (HSTS, CSP, etc.)

3. **Monitoring Setup**
   - [ ] Add Prometheus metrics for API server
   - [ ] Configure alerting for executor disconnections
   - [ ] Monitor certificate expiry dates

4. **Future Enhancements**
   - [ ] Add OAuth/OIDC for human users
   - [ ] Implement multi-region deployment
   - [ ] Add API versioning
   - [ ] Implement WebSocket fallback for A2A

## Potential Issues and Mitigations

### Issue: Let's Encrypt Rate Limits
- **Problem**: Let's Encrypt has rate limits (50 certs per domain per week)
- **Mitigation**: Use staging issuer first, then switch to production
- **Fix**: If hit rate limit, wait 1 week or use different domain

### Issue: DNS Propagation Delay
- **Problem**: DNS changes may take time to propagate
- **Mitigation**: Set low TTL (300s) on A record
- **Fix**: Wait 15-30 minutes, test with `dig` and different DNS servers

### Issue: Executor SSL Verification Fails
- **Problem**: Let's Encrypt cert not trusted by executor
- **Mitigation**: Use system CA bundle (should work by default)
- **Fix**: Set `KUBENTLY_SSL_VERIFY=false` temporarily (not recommended for prod)

### Issue: GKE Image Pull Failures
- **Problem**: Images not available in GKE cluster
- **Mitigation**: Push to GCR or Docker Hub before deployment
- **Fix**: Use imagePullPolicy: IfNotPresent and pre-pull images to nodes

### Issue: Cross-cluster Communication
- **Problem**: Kind executor can't reach GKE API over HTTPS
- **Mitigation**: Test connectivity with curl from Kind pod
- **Fix**: Check firewall rules, DNS resolution, and certificate validation

## Notes

- This is the first production-grade deployment test
- Previous testing was limited to local Kind cluster with self-signed certs
- This validates the full deployment architecture including:
  - Public DNS resolution
  - Let's Encrypt certificate provisioning
  - Multi-cluster executor registration
  - HTTPS communication between clusters
  - CLI interaction with production API

## Code Readiness Assessment

Based on the code review, the following components are ready:

✅ **Helm Charts**
- TLS configuration supports both internal (self-signed) and external (Let's Encrypt) modes
- Ingress template properly configured for nginx and cert-manager
- Certificate and ClusterIssuer templates available
- Values files support external domain and issuer configuration

✅ **Executor**
- SSE executor supports custom API URL via `KUBENTLY_API_URL` env var
- SSL verification configurable via `KUBENTLY_SSL_VERIFY`
- CA certificate support via `KUBENTLY_CA_CERT`
- Token-based authentication implemented

✅ **API Server**
- A2A protocol mounted at `/a2a/` path
- Health endpoint available
- Token authentication via configmap
- Supports multiple executors with different cluster IDs

⚠️ **Potential Issues**
1. **Image Registry**: Need to push images to GCR or Docker Hub for GKE access
2. **Token Management**: Need to manually add Kind executor token to GKE configmap
3. **DNS Validation**: Let's Encrypt HTTP-01 challenge requires DNS to be configured first

## Timeline

- **Phase 1 (Infrastructure)**: 30-45 minutes
  - cert-manager installation: 5 minutes
  - nginx-ingress installation: 5 minutes
  - Let's Encrypt issuer setup: 2 minutes
  - DNS configuration: 5-30 minutes (propagation time)

- **Phase 2 (GKE Deployment)**: 20-30 minutes
  - Image preparation: 10 minutes
  - Helm deployment: 5 minutes
  - Certificate provisioning: 5-15 minutes

- **Phase 3 (Kind Executor)**: 15-20 minutes
  - Token generation and registration: 5 minutes
  - Executor deployment: 5 minutes
  - Testing connectivity: 5 minutes

- **Phase 4 (Testing)**: 30-60 minutes
  - API endpoint testing: 10 minutes
  - CLI testing: 20 minutes
  - End-to-end scenarios: 30 minutes

**Total Estimated Time**: 2-3 hours (including troubleshooting buffer)
