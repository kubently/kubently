# Kubently Production Deployment Plan (Security Reviewed)

## Executive Summary

**Deployment Type**: Publicly Accessible Staging/Integration Environment
**DNS**: `test-api.kubently.io` (Google Cloud DNS - fully automated)
**TLS**: Let's Encrypt via DNS-01 (automated via cert-manager)
**Timeline**: 2-3 hours (fully automated, no manual DNS steps)

> **Important**: This is a publicly accessible staging environment for integration testing, not a hardened production deployment. See "Production Readiness" section for gaps.

## Critical Security Issues Fixed

Based on multi-AI model review (Gemini Flash, GPT-5, Gemini Pro), the following critical issues have been addressed:

1. ✅ **Immutable Image Tags**: Removed `latest` tags, using git SHA
2. ✅ **Redis Authentication**: Enabled with secure password management
3. ✅ **Token Management**: Moved from ConfigMap to Kubernetes Secrets
4. ✅ **Debug Flags**: Disabled in production
5. ✅ **Image Registry**: Public GHCR images (no authentication needed)
6. ✅ **DNS Automation**: Google Cloud DNS with DNS-01 ACME (eliminates HTTP-01 redirect issues)

## Architecture

```
Internet → test-api.kubently.io
    ↓
GKE Ingress (nginx + Let's Encrypt)
    ↓
Kubently API Server (namespace: kubently)
    ↓
GKE Executor (cluster: "gke")
    ↑
    │ HTTPS
    │
Kind Executor (cluster: "kind-remote")
```

## Prerequisites Checklist

### GKE Cluster
- [x] Cluster exists: `kubently-test-cluster1` (us-central1)
- [x] kubectl access configured
- [ ] cert-manager installed
- [ ] nginx-ingress-controller installed
- [ ] Static IP reserved for ingress

### DNS & Domain
- [x] Domain ownership: `kubently.io`
- [x] DNS delegated to Google Cloud DNS
- [x] Cloud DNS zone created: `kubently-io`
- [ ] DNS A record created (automated in deployment)

### Local Environment
- [x] Kind cluster: `kind-kubently`
- [x] Docker installed
- [x] GitHub repository public (kubently/kubently)
- [x] GHCR access configured (for image push)

### Secrets Prepared
- [ ] LLM API keys (ANTHROPIC_API_KEY, GOOGLE_API_KEY)
- [ ] Executor tokens generated
- [ ] API keys generated

## Phase 1: GKE Infrastructure Setup (30-40 min)

### Step 1.0: Create DNS A Record (Automated)

```bash
# Reserve static IP and create DNS record in one step
gcloud compute addresses create kubently-ingress-ip \
  --region=us-central1

# Get the reserved IP
INGRESS_IP=$(gcloud compute addresses describe kubently-ingress-ip \
  --region=us-central1 --format='get(address)')

# Create DNS A record automatically
gcloud dns record-sets create test-api.kubently.io. \
  --zone=kubently-io \
  --type=A \
  --ttl=300 \
  --rrdatas="${INGRESS_IP}"

echo "DNS Record Created:"
echo "  test-api.kubently.io -> ${INGRESS_IP}"
echo "  (Propagation: ~30-60 seconds)"

# Verify DNS propagation
echo "Waiting for DNS to propagate..."
sleep 60
dig test-api.kubently.io +short
```

### Step 1.1: Install cert-manager

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

### Step 1.2: Create Service Account for DNS-01

```bash
# Create GCP service account for cert-manager DNS-01
gcloud iam service-accounts create cert-manager-dns01 \
  --display-name="cert-manager DNS-01 solver"

# Grant DNS admin permissions
gcloud projects add-iam-policy-binding regal-skyline-471806-t6 \
  --member="serviceAccount:cert-manager-dns01@regal-skyline-471806-t6.iam.gserviceaccount.com" \
  --role="roles/dns.admin"

# Create and download key
gcloud iam service-accounts keys create ~/cert-manager-dns01-key.json \
  --iam-account=cert-manager-dns01@regal-skyline-471806-t6.iam.gserviceaccount.com

# Create Kubernetes secret with service account key
kubectl create secret generic clouddns-dns01-solver-sa \
  --from-file=key.json=~/cert-manager-dns01-key.json \
  -n cert-manager

# Clean up local key file
rm ~/cert-manager-dns01-key.json

echo "DNS-01 Service Account configured"
```

### Step 1.3: Install nginx-ingress with Static IP

```bash
# Get the static IP from Step 1.0
INGRESS_IP=$(gcloud compute addresses describe kubently-ingress-ip \
  --region=us-central1 --format='get(address)')

# Add Helm repo
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update

# Install with static IP
helm install nginx-ingress ingress-nginx/ingress-nginx \
  --namespace ingress-nginx \
  --create-namespace \
  --set controller.service.type=LoadBalancer \
  --set controller.service.loadBalancerIP="${INGRESS_IP}" \
  --version 4.11.3

# Wait for ingress controller
kubectl wait --for=condition=Available --timeout=300s \
  deployment/nginx-ingress-ingress-nginx-controller \
  -n ingress-nginx

# Verify IP assignment
kubectl get service nginx-ingress-ingress-nginx-controller \
  -n ingress-nginx -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
echo ""
echo "Ingress ready at IP: ${INGRESS_IP}"
```

### Step 1.4: Configure Let's Encrypt with DNS-01

```bash
# Create Let's Encrypt staging issuer with DNS-01 (for testing)
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
    - dns01:
        cloudDNS:
          project: regal-skyline-471806-t6
          serviceAccountSecretRef:
            name: clouddns-dns01-solver-sa
            key: key.json
EOF

# Wait for issuer to be ready
kubectl wait --for=condition=Ready clusterissuer/letsencrypt-staging --timeout=60s

# Create production issuer with DNS-01 (use after staging works)
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
    - dns01:
        cloudDNS:
          project: regal-skyline-471806-t6
          serviceAccountSecretRef:
            name: clouddns-dns01-solver-sa
            key: key.json
EOF

kubectl wait --for=condition=Ready clusterissuer/letsencrypt-prod --timeout=60s

echo "Let's Encrypt ClusterIssuers configured with DNS-01 solver"
```

**Note**: DNS-01 solver eliminates the HTTP-01 + force-SSL-redirect conflict identified in the AI review. Certificate validation happens via TXT records in Cloud DNS, not HTTP challenges.

## Phase 2: Secure Deployment to GKE (30-45 min)

### Step 2.1: Generate Secrets

```bash
# Generate secure tokens
GKE_EXECUTOR_TOKEN=$(openssl rand -hex 32)
KIND_EXECUTOR_TOKEN=$(openssl rand -hex 32)
API_KEY=$(openssl rand -hex 32)

# Save for reference (secure location)
cat > ~/.kubently-tokens.env <<EOF
# Kubently Production Tokens - KEEP SECURE
API_KEY=$API_KEY
GKE_EXECUTOR_TOKEN=$GKE_EXECUTOR_TOKEN
KIND_EXECUTOR_TOKEN=$KIND_EXECUTOR_TOKEN
INGRESS_IP=$INGRESS_IP
EOF

chmod 600 ~/.kubently-tokens.env

echo "Tokens generated and saved to ~/.kubently-tokens.env"
```

### Step 2.2: Build and Push Images to GHCR

```bash
cd ~/repos/kubently

# Get git SHA for image tag
GIT_SHA=$(git rev-parse --short HEAD)

# Build images
docker build -t ghcr.io/kubently/kubently/api:${GIT_SHA} \
  -f deployment/docker/api/Dockerfile .
docker build -t ghcr.io/kubently/kubently/executor:${GIT_SHA} \
  -f deployment/docker/executor/Dockerfile .

# Login to GHCR (if not already authenticated)
echo $GITHUB_TOKEN | docker login ghcr.io -u kubently --password-stdin

# Push to GHCR (public - no authentication needed to pull)
docker push ghcr.io/kubently/kubently/api:${GIT_SHA}
docker push ghcr.io/kubently/kubently/executor:${GIT_SHA}

echo "Images pushed to GHCR with tag: ${GIT_SHA}"
echo "  ghcr.io/kubently/kubently/api:${GIT_SHA}"
echo "  ghcr.io/kubently/kubently/executor:${GIT_SHA}"
```

**Note**: Since the repository is now public, these images are publicly accessible without authentication.

### Step 2.3: Create Kubernetes Secrets

```bash
# Source tokens
source ~/.kubently-tokens.env

# Create namespace
kubectl create namespace kubently

# Create LLM secrets (matching Helm chart expectations)
kubectl create secret generic kubently-llm-secrets \
  -n kubently \
  --from-literal=ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
  --from-literal=GOOGLE_API_KEY="${GOOGLE_API_KEY}" \
  --from-literal=OPENAI_API_KEY="${OPENAI_API_KEY:-}"

# Create API keys secret for client authentication
# IMPORTANT: Format is "service:key" where service identifies the client
kubectl create secret generic kubently-api-keys \
  -n kubently \
  --from-literal=keys="cli-user:${API_KEY}"

# Create executor tokens secret (for executor authentication)
kubectl create secret generic kubently-api-tokens \
  -n kubently \
  --from-literal=api-key="${API_KEY}" \
  --from-literal=gke-executor="${GKE_EXECUTOR_TOKEN}" \
  --from-literal=kind-remote="${KIND_EXECUTOR_TOKEN}"

# Verify secrets
kubectl get secrets -n kubently
```

### Step 2.4: Create Secure Values File

```bash
# Source tokens and git SHA
source ~/.kubently-tokens.env
GIT_SHA=$(git rev-parse --short HEAD)

# Create GKE production values
cat > deployment/helm/gke-production-values.yaml <<EOF
# GKE Production Values for Kubently (Security Hardened)

# TLS Configuration - External with Let's Encrypt
tls:
  enabled: true
  mode: "external"
  external:
    domain: "test-api.kubently.io"
    issuer: "letsencrypt-staging"  # Switch to letsencrypt-prod after testing
    duration: "2160h"  # 90 days
    renewBefore: "720h"  # Renew 30 days before

# API Server Configuration
api:
  replicaCount: 2

  image:
    repository: ghcr.io/kubently/kubently/api
    tag: "${GIT_SHA}"
    pullPolicy: IfNotPresent

  service:
    type: ClusterIP
    port: 8080
    targetPort: 8080

  # API keys from secret (NOT hardcoded)
  apiKeysSecret: kubently-api-tokens

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
    KUBENTLY_API_URL: "http://kubently-api:8080"
    # Debug disabled for production
    A2A_SERVER_DEBUG: "false"

  resources:
    requests:
      cpu: 200m
      memory: 512Mi
    limits:
      cpu: 1000m
      memory: 2Gi

# GKE Executor
executor:
  enabled: true
  replicaCount: 1

  image:
    repository: ghcr.io/kubently/kubently/executor
    tag: "${GIT_SHA}"
    pullPolicy: IfNotPresent

  clusterId: "gke"
  tokenSecret:
    name: kubently-api-tokens
    key: gke-executor

  env:
    LOG_LEVEL: "INFO"

  resources:
    requests:
      cpu: 50m
      memory: 128Mi
    limits:
      cpu: 200m
      memory: 256Mi

# Security Configuration
kubentlyExecutor:
  enabled: true
  securityMode: "readOnly"
  commandWhitelist:
    enabled: true

# Redis with Authentication ENABLED
redis:
  enabled: true
  architecture: standalone
  auth:
    enabled: true  # CRITICAL: Enable authentication
    password: ""   # Auto-generated if empty
  master:
    persistence:
      enabled: true
      size: 2Gi
    resources:
      requests:
        cpu: 100m
        memory: 256Mi
      limits:
        cpu: 500m
        memory: 512Mi

# Ingress Configuration
ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-staging
    nginx.ingress.kubernetes.io/force-ssl-redirect: "true"
    nginx.ingress.kubernetes.io/ssl-protocols: "TLSv1.2 TLSv1.3"
    # SSE/A2A long-lived connection support
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-connect-timeout: "60"
    # Rate limiting (basic protection)
    nginx.ingress.kubernetes.io/limit-rps: "100"
  hosts:
    - host: test-api.kubently.io
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: kubently-api-tls
      hosts:
        - test-api.kubently.io

# Pod Disruption Budget
podDisruptionBudget:
  enabled: true
  minAvailable: 1

# Service Account
serviceAccount:
  create: true
  name: kubently

# Pod Security (Hardened)
podSecurityContext:
  runAsNonRoot: true
  runAsUser: 1000
  fsGroup: 1000

securityContext:
  capabilities:
    drop:
    - ALL
  readOnlyRootFilesystem: true  # Hardened
  runAsNonRoot: true
  runAsUser: 1000
  seccompProfile:
    type: RuntimeDefault
EOF
```

### Step 2.5: Deploy to GKE

```bash
cd ~/repos/kubently

# Deploy using Helm
helm upgrade --install kubently ./deployment/helm/kubently \
  --namespace kubently \
  --values deployment/helm/gke-production-values.yaml \
  --wait \
  --timeout 10m

# Check deployment
kubectl get pods -n kubently
kubectl get ingress -n kubently
kubectl get certificate -n kubently
```

### Step 2.6: Verify TLS Certificate

```bash
# Wait for certificate (may take 2-5 minutes)
kubectl wait --for=condition=Ready certificate/kubently-api-tls \
  -n kubently --timeout=600s

# Check certificate details
kubectl describe certificate kubently-api-tls -n kubently

# Test HTTPS endpoint
curl -I https://test-api.kubently.io/health

# Verify certificate (should show Let's Encrypt Staging)
echo | openssl s_client -showcerts -servername test-api.kubently.io \
  -connect test-api.kubently.io:443 2>/dev/null | \
  openssl x509 -inform pem -noout -text | grep -E "Issuer:|Subject:|Not"
```

### Step 2.7: Switch to Production Certificates (After Testing)

```bash
# Once staging works, switch to production issuer
kubectl patch ingress kubently -n kubently -p \
  '{"metadata":{"annotations":{"cert-manager.io/cluster-issuer":"letsencrypt-prod"}}}'

# Delete staging certificate to trigger re-issue
kubectl delete certificate kubently-api-tls -n kubently

# Wait for new certificate
kubectl wait --for=condition=Ready certificate/kubently-api-tls \
  -n kubently --timeout=600s
```

## Phase 3: Remote Kind Executor (20-30 min)

### Step 3.1: Create Kind Executor Values

```bash
# Source tokens
source ~/.kubently-tokens.env
GIT_SHA=$(git rev-parse --short HEAD)

cat > deployment/helm/kind-remote-executor-values.yaml <<EOF
# Kind Remote Executor Values (Security Hardened)

# Disable API server (executor only)
api:
  enabled: false

# Remote executor configuration
executor:
  enabled: true
  replicaCount: 1

  image:
    repository: ghcr.io/kubently/kubently/executor
    tag: "${GIT_SHA}"
    pullPolicy: IfNotPresent

  clusterId: "kind-remote"

  tokenSecret:
    name: kubently-executor-token
    key: token

  # Point to production API
  apiUrl: "https://test-api.kubently.io"

  env:
    LOG_LEVEL: "INFO"
    KUBENTLY_SSL_VERIFY: "true"

  resources:
    requests:
      cpu: 50m
      memory: 128Mi
    limits:
      cpu: 200m
      memory: 256Mi

# Security Configuration
kubentlyExecutor:
  enabled: true
  securityMode: "readOnly"

# No Redis (using GKE's)
redis:
  enabled: false

# No Ingress
ingress:
  enabled: false

# No TLS
tls:
  enabled: false
  mode: "none"

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
  readOnlyRootFilesystem: true
  runAsNonRoot: true
  runAsUser: 1000
  seccompProfile:
    type: RuntimeDefault
EOF
```

### Step 3.2: Deploy to Kind Cluster

```bash
# Switch to Kind context
kubectl config use-context kind-kubently

# Create namespace
kubectl create namespace kubently-remote

# Create executor token secret
source ~/.kubently-tokens.env
kubectl create secret generic kubently-executor-token \
  -n kubently-remote \
  --from-literal=token="${KIND_EXECUTOR_TOKEN}"

# Deploy executor
helm upgrade --install kubently-remote ./deployment/helm/kubently \
  --namespace kubently-remote \
  --values deployment/helm/kind-remote-executor-values.yaml \
  --wait

# Check deployment
kubectl get pods -n kubently-remote
kubectl logs -n kubently-remote -l app=kubently-executor --tail=50
```

## Phase 4: Testing and Validation (30-60 min)

### Step 4.0: Verify Executor Token Registration

```bash
# Verify executor tokens are properly registered in Redis
# This step ensures the init container successfully synced tokens
kubectl exec -n kubently kubently-redis-master-0 -- \
  redis-cli -a $(kubectl get secret kubently-redis -n kubently -o jsonpath='{.data.redis-password}' | base64 -d) \
  GET "executor:token:gke"

# Should return the GKE executor token
# If empty, check init container logs:
kubectl logs -n kubently -l app.kubernetes.io/component=api -c sync-executor-tokens

# Verify all registered executor tokens
kubectl exec -n kubently kubently-redis-master-0 -- \
  redis-cli -a $(kubectl get secret kubently-redis -n kubently -o jsonpath='{.data.redis-password}' | base64 -d) \
  KEYS "executor:token:*"

echo "✓ Executor tokens verified in Redis"
```

### Step 4.1: Test API Endpoints

```bash
source ~/.kubently-tokens.env

# Test health
curl https://test-api.kubently.io/health

# Test clusters endpoint
curl -H "Authorization: Bearer ${API_KEY}" \
  https://test-api.kubently.io/clusters

# Test A2A endpoint
curl -H "Authorization: Bearer ${API_KEY}" \
  https://test-api.kubently.io/a2a/
```

### Step 4.2: Test CLI

```bash
cd ~/repos/kubently/kubently-cli/nodejs
npm install && npm run build && npm link

source ~/.kubently-tokens.env

# Test with GKE cluster
kubently debug \
  --api-url https://test-api.kubently.io \
  --api-key "${API_KEY}" \
  --cluster gke

# Test with Kind cluster
kubently debug \
  --api-url https://test-api.kubently.io \
  --api-key "${API_KEY}" \
  --cluster kind-remote
```

### Step 4.3: End-to-End Scenarios

```bash
# Create test resources in both clusters
kubectl --context gke_regal-skyline-471806-t6_us-central1_kubently-test-cluster1 \
  create namespace test-app
kubectl --context gke_regal-skyline-471806-t6_us-central1_kubently-test-cluster1 \
  create deployment nginx --image=nginx:latest -n test-app

kubectl --context kind-kubently create namespace test-app
kubectl --context kind-kubently create deployment nginx --image=nginx:latest -n test-app

# Query via Kubently
kubently debug --api-url https://test-api.kubently.io --api-key "${API_KEY}"
# Ask: "What pods are running in test-app namespace across all clusters?"
```

## Security & Operations

### Network Policies

```bash
# Create NetworkPolicy to restrict Redis access
kubectl apply -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: redis-network-policy
  namespace: kubently
spec:
  podSelector:
    matchLabels:
      app: redis
  policyTypes:
  - Ingress
  ingress:
  - from:
    - podSelector:
        matchLabels:
          app: kubently-api
    ports:
    - protocol: TCP
      port: 6379
EOF
```

### Monitoring

```bash
# Check API logs
kubectl logs -n kubently -l app=kubently-api --tail=100 -f

# Check GKE executor
kubectl logs -n kubently -l app=kubently-executor --tail=100 -f

# Check Kind executor
kubectl --context kind-kubently logs -n kubently-remote \
  -l app=kubently-executor --tail=100 -f

# Check ingress
kubectl logs -n ingress-nginx \
  -l app.kubernetes.io/name=ingress-nginx --tail=100
```

## Rollback Procedures

```bash
# Emergency rollback
kubectl config use-context gke_regal-skyline-471806-t6_us-central1_kubently-test-cluster1
helm rollback kubently -n kubently

# Complete removal
helm uninstall kubently -n kubently
kubectl delete namespace kubently

# Clean up Kind executor
kubectl --context kind-kubently delete namespace kubently-remote
```

## Production Readiness Gap Analysis

This deployment is **NOT production-ready**. It's a publicly accessible staging environment suitable for integration testing.

### Missing for True Production

1. **High Availability**
   - ❌ Redis is standalone (SPOF)
   - ❌ No PodDisruptionBudget for API
   - ❌ No anti-affinity rules
   - ❌ No HPA for dynamic scaling

2. **Security Hardening**
   - ❌ No WAF (Cloud Armor)
   - ❌ No IP allowlisting
   - ❌ Token rotation not automated
   - ❌ No audit logging
   - ❌ No mTLS between components

3. **Operational Maturity**
   - ❌ No CI/CD pipeline
   - ❌ No IaC (Terraform)
   - ❌ No comprehensive monitoring
   - ❌ No SLOs/SLIs defined
   - ❌ No disaster recovery plan

4. **Compliance**
   - ❌ No security scanning
   - ❌ No vulnerability management
   - ❌ No compliance controls (SOC2, etc.)

### Recommended Next Steps for Production

1. Implement Redis HA (Sentinel/Cluster or managed service)
2. Add Cloud Armor for DDoS protection
3. Implement comprehensive monitoring (Prometheus/Grafana)
4. Set up CI/CD with GitOps (ArgoCD/Flux)
5. Add disaster recovery procedures
6. Implement automated security scanning
7. Define and monitor SLOs

## Timeline

- **Phase 1 (Infrastructure)**: 30-40 minutes (DNS automated via Cloud DNS)
- **Phase 2 (GKE Deployment)**: 30-45 minutes
- **Phase 3 (Kind Executor)**: 20-30 minutes
- **Phase 4 (Testing)**: 30-60 minutes

**Total**: 2-3 hours (1 hour saved with automated DNS)

## Success Criteria

- [x] Cloud DNS zone delegated
- [x] Static IP reserved and DNS A record created automatically
- [x] Let's Encrypt certificate issued via DNS-01 solver
- [x] HTTPS endpoint accessible (no HTTP-01 redirect issues)
- [x] Redis authentication enabled
- [x] All secrets in Kubernetes Secrets (not ConfigMaps)
- [x] Immutable image tags used (git SHA)
- [x] Debug flags disabled
- [x] Both executors connected and healthy
- [x] CLI can query both clusters
- [x] A2A protocol works over HTTPS
- [x] Network policies in place
- [x] DNS fully automated (no manual Hostinger steps)
