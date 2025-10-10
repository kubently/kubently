# Multi-Cluster TLS Deployment Guide

## Overview

This guide covers deploying Kubently with TLS in various scenarios, including same-cluster, cross-cluster, and hybrid deployments.

## Deployment Scenarios

### Scenario 1: Same Cluster with Internal TLS

**Use Case**: Development/testing environment where everything runs in one cluster.

**Configuration**:
```yaml
# values.yaml
tls:
  enabled: true
  mode: "internal"  # Self-signed certificates

executor:
  enabled: true
  # No custom apiUrl needed - uses internal service
```

**How it works**:
- Executor connects via HTTP to `kubently-api:8080` (pod-to-service)
- External access via ingress uses TLS with self-signed cert
- No cross-cluster networking needed

### Scenario 2: Same Cluster with Public TLS

**Use Case**: Production deployment in a single cluster with public certificates.

**Configuration**:
```yaml
# values.yaml
tls:
  enabled: true
  mode: "external"
  external:
    domain: "api.kubently.example.com"
    issuer: "letsencrypt-prod"

executor:
  enabled: true
  # Executor will use https://api.kubently.example.com
```

**How it works**:
- DNS points to ingress (could be internal or external IP)
- Executor connects via HTTPS using public domain
- Certificate validated using public trust chain
- Works even if DNS resolves to internal IP

### Scenario 3: Cross-Cluster with Public TLS

**Use Case**: API in cluster A, executors in clusters B, C, D.

**API Cluster Configuration**:
```yaml
# api-cluster-values.yaml
tls:
  enabled: true
  mode: "external"
  external:
    domain: "api.kubently.example.com"
    issuer: "letsencrypt-prod"

executor:
  enabled: false  # No executor in API cluster

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: api.kubently.example.com
      paths:
        - path: /
          pathType: Prefix
```

**Executor Cluster Configuration**:
```yaml
# executor-cluster-values.yaml
api:
  enabled: false  # No API in executor clusters

executor:
  enabled: true
  clusterId: "production-cluster-b"  # Unique per cluster
  apiUrl: "https://api.kubently.example.com"  # Explicit API URL
  token: "your-executor-token"

# No TLS configuration needed in executor clusters
tls:
  enabled: false
```

**Deploy Commands**:
```bash
# In API cluster
helm install kubently-api ./deployment/helm/kubently \
  -f api-cluster-values.yaml

# In each executor cluster
helm install kubently-executor ./deployment/helm/kubently \
  -f executor-cluster-values.yaml \
  --set executor.clusterId="cluster-b"
```

### Scenario 4: Hybrid Deployment (Same + Cross Cluster)

**Use Case**: API with local executor + remote executors in other clusters.

**API Cluster Configuration**:
```yaml
# hybrid-api-values.yaml
tls:
  enabled: true
  mode: "external"
  external:
    domain: "api.kubently.internal.company.com"  # Internal DNS
    issuer: "internal-ca"  # Company CA

executor:
  enabled: true
  clusterId: "api-cluster"
  # For same-cluster executor, can optimize with internal URL
  apiUrl: "http://kubently-api:8080"  # Override to use internal connection
```

**Remote Executor Configuration**:
```yaml
# remote-executor-values.yaml
executor:
  enabled: true
  clusterId: "remote-cluster-1"
  apiUrl: "https://api.kubently.internal.company.com"
  token: "remote-executor-token"
```

### Scenario 5: Multi-Region with Geographic Load Balancing

**Use Case**: Global deployment with regional API endpoints.

**Configuration**:
```yaml
# Region-specific configurations
# us-east values
tls:
  external:
    domain: "api-us.kubently.example.com"

# eu-west values  
tls:
  external:
    domain: "api-eu.kubently.example.com"

# Executors can connect to nearest region
executor:
  apiUrl: "https://api-us.kubently.example.com"  # or use GeoDNS
```

## Network Architecture Patterns

### Pattern 1: Internal Load Balancer with Public Cert

```
Internet → Firewall → Internal LB → Ingress (TLS) → API Service
                           ↑
                    Internal DNS
                    (api.internal.com)
                           ↑
                       Executors
```

**Benefits**:
- Public certificates work (valid domain)
- No internet exposure
- Cross-cluster communication via internal network

### Pattern 2: Service Mesh Integration

```
Executor → Istio Gateway → API Service
         (mTLS)          (HTTP)
```

**Configuration with Istio**:
```yaml
executor:
  apiUrl: "https://kubently-api.istio-system.svc.cluster.local"
  env:
    KUBENTLY_SSL_VERIFY: "true"
    # Istio handles mTLS automatically
```

### Pattern 3: Private Certificate Authority

For organizations with internal CA:

```yaml
tls:
  mode: "external"
  external:
    domain: "api.kubently.internal"
    issuer: "corporate-ca-issuer"

executor:
  apiUrl: "https://api.kubently.internal"
  # Mount corporate CA bundle
  volumes:
    - name: ca-bundle
      configMap:
        name: corporate-ca-bundle
```

## Best Practices

### 1. Certificate Management

**For Production**:
- Always use `external` mode with proper certificates
- Set up monitoring for certificate expiry
- Use cert-manager for automatic renewal

**For Development**:
- `internal` mode is fine for single cluster
- For multi-cluster dev, use `external` with Let's Encrypt staging

### 2. Network Security

**Recommended Setup**:
```yaml
# Restrict API access
networkPolicy:
  enabled: true
  ingress:
    - from:
      - namespaceSelector:
          matchLabels:
            name: kubently
      - podSelector:
          matchLabels:
            app: executor
```

### 3. Token Management

**Per-Cluster Tokens**:
```bash
# Generate unique token per executor cluster
EXECUTOR_TOKEN=$(openssl rand -hex 32)

# Store in API cluster
kubectl exec -n kubently kubently-redis-master-0 -- \
  redis-cli SET "executor:token:cluster-b" "$EXECUTOR_TOKEN"

# Use in executor cluster
helm install kubently-executor ./helm/kubently \
  --set executor.token="$EXECUTOR_TOKEN"
```

### 4. Monitoring Cross-Cluster Connectivity

**Health Checks**:
```yaml
executor:
  env:
    HEALTH_CHECK_INTERVAL: "30"
    CONNECTION_TIMEOUT: "10"
```

**Prometheus Metrics**:
```yaml
serviceMonitor:
  enabled: true
  endpoints:
    - port: metrics
      interval: 30s
```

## Troubleshooting

### Issue: Executor Can't Connect Cross-Cluster

**Check DNS Resolution**:
```bash
# From executor pod
kubectl exec -it <executor-pod> -- nslookup api.kubently.example.com
```

**Check Network Connectivity**:
```bash
# Test HTTPS connection
kubectl exec -it <executor-pod> -- curl -v https://api.kubently.example.com/health
```

**Check Certificate Validation**:
```bash
# Verify certificate
kubectl exec -it <executor-pod> -- openssl s_client -connect api.kubently.example.com:443
```

### Issue: Certificate Validation Fails

**For Internal CA**:
```yaml
executor:
  extraVolumes:
    - name: ca-cert
      configMap:
        name: internal-ca
  extraVolumeMounts:
    - name: ca-cert
      mountPath: /etc/ssl/certs/
  env:
    SSL_CERT_DIR: "/etc/ssl/certs"
```

### Issue: Same-Cluster Optimization Not Working

**Use Explicit Internal URL**:
```yaml
executor:
  # Force internal connection for same-cluster
  apiUrl: "http://kubently-api.kubently.svc.cluster.local:8080"
```

## Migration Strategies

### From Single to Multi-Cluster

1. **Phase 1**: Deploy with `internal` mode in single cluster
2. **Phase 2**: Switch to `external` mode with public/internal domain
3. **Phase 3**: Deploy executors to remote clusters
4. **Phase 4**: Remove local executor if not needed

### Rolling Certificate Updates

```bash
# Update certificate without downtime
kubectl create secret tls kubently-api-tls-new --cert=new.crt --key=new.key
kubectl patch ingress kubently -p '{"spec":{"tls":[{"secretName":"kubently-api-tls-new"}]}}'
```

## Security Considerations

### Do's ✅
- Use public certificates for any cross-cluster communication
- Implement network policies
- Use unique tokens per executor/cluster
- Monitor certificate expiry
- Use mTLS if available (service mesh)

### Don'ts ❌
- Don't use `internal` mode for cross-cluster
- Don't disable SSL verification in production
- Don't expose API without authentication
- Don't use same token across environments

## Summary

The TLS design supports multiple deployment patterns:

| Scenario | TLS Mode | Executor URL | Works Cross-Cluster |
|----------|----------|--------------|-------------------|
| Dev/Test Single Cluster | `internal` | `http://service:8080` | ❌ |
| Prod Single Cluster | `external` | `https://domain` | ✅ |
| Multi-Cluster | `external` | `https://domain` | ✅ |
| Hybrid | `external` + custom | Mixed | ✅ |

The key is using `external` mode with proper domains for any production or cross-cluster deployment.