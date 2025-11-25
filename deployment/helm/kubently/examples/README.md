# TLS Configuration Examples

This directory contains examples for setting up TLS with kubently. The Helm chart follows the "user brings certificate" pattern (Bitnami/ArgoCD standard), which means **you must create TLS certificates separately** and reference them in your values.

## Quick Reference

| Use Case | Example File | Setup Time | Production Ready |
|----------|-------------|------------|------------------|
| Production with cert-manager | `tls-cert-manager.yaml` | ~5 min | ✅ Yes |
| Development/Testing | `tls-dev-selfsigned.yaml` | ~2 min | ❌ No (self-signed) |
| Manual/Existing Certificate | `tls-manual-cert.yaml` | ~1 min | ✅ Yes (if cert is valid) |
| Cloud Provider (AWS ACM/GCP) | `tls-cloud-lb.yaml` | ~10 min | ✅ Yes |
| Service Mesh (Istio) | `tls-istio-gateway.yaml` | ~15 min | ✅ Yes |

## No TLS (Default)

For local development with port-forwarding, TLS is not required:

```bash
# Deploy without ingress
helm install kubently ./deployment/helm/kubently \
  -f deployment/helm/test-values.yaml

# Access via port-forward
kubectl port-forward svc/kubently-api 8080:8080 -n kubently

# Use HTTP
curl http://localhost:8080/health
```

## Examples

### 1. cert-manager with ClusterIssuer (Recommended for Production)

**Prerequisites:**
- cert-manager installed in cluster (`kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.13.0/cert-manager.yaml`)
- ClusterIssuer configured (Let's Encrypt example in `tls-cert-manager.yaml`)

**Use when:**
- You want automatic certificate renewal
- You have a public domain
- You're using nginx/traefik/other standard ingress

See: [`tls-cert-manager.yaml`](./tls-cert-manager.yaml)

### 2. Development Self-Signed (Testing Only)

**Prerequisites:**
- cert-manager installed (optional, uses namespace-scoped Issuer)

**Use when:**
- Local development/testing
- CI/CD preview environments
- You don't have a public domain

**⚠️ Warning:** Browsers will show security warnings. Not for production!

See: [`tls-dev-selfsigned.yaml`](./tls-dev-selfsigned.yaml)

### 3. Manual/Existing Certificate

**Prerequisites:**
- You have a TLS certificate file (`.crt`) and private key (`.key`)

**Use when:**
- You have existing certificates from CA
- Using enterprise PKI
- Certificates managed externally

See: [`tls-manual-cert.yaml`](./tls-manual-cert.yaml)

### 4. Cloud Provider Load Balancer

**Prerequisites:**
- AWS: ALB Ingress Controller + ACM certificate
- GCP: GCLB + Google-managed certificate
- Azure: Application Gateway + Key Vault certificate

**Use when:**
- TLS terminates at cloud load balancer (not in cluster)
- Using cloud-native certificate management

See: [`tls-cloud-lb.yaml`](./tls-cloud-lb.yaml)

### 5. Service Mesh (Istio)

**Prerequisites:**
- Istio installed with Ingress Gateway

**Use when:**
- Using Istio for traffic management
- Want mTLS between services
- Advanced traffic routing needs

See: [`tls-istio-gateway.yaml`](./tls-istio-gateway.yaml)

## Troubleshooting

### Certificate not being created

```bash
# Check cert-manager logs
kubectl logs -n cert-manager deploy/cert-manager

# Check Certificate status
kubectl describe certificate kubently-api-tls -n kubently

# Check ClusterIssuer status
kubectl describe clusterissuer letsencrypt-prod
```

### Ingress not getting TLS secret

```bash
# Verify secret exists
kubectl get secret kubently-api-tls -n kubently

# Check ingress
kubectl describe ingress kubently -n kubently

# Verify secret has correct keys
kubectl get secret kubently-api-tls -n kubently -o jsonpath='{.data}' | jq 'keys'
# Should show: ["tls.crt", "tls.key"]
```

### Browser shows "Not Secure"

- **Self-signed certificates:** Expected, add exception in browser
- **Let's Encrypt:** Check domain DNS points to ingress IP
- **Expired certificate:** Check `kubectl get certificate` shows READY=True

## More Information

- [cert-manager docs](https://cert-manager.io/docs/)
- [Kubernetes Ingress TLS](https://kubernetes.io/docs/concepts/services-networking/ingress/#tls)
- [Let's Encrypt](https://letsencrypt.org/)
