# TLS Deployment Guide

## Overview

Kubently now supports TLS encryption by default for secure communication between the executor and API. The system supports three TLS modes:

1. **internal** (default) - Self-signed certificates for development/testing
2. **external** - Public certificates for production deployments  
3. **none** - HTTP-only mode (not recommended)

## Quick Start

### Default Deployment (with Internal TLS)

```bash
./deploy-test.sh
```

This will:
- Install cert-manager if not present
- Install nginx ingress controller if not present
- Generate self-signed certificates
- Deploy Kubently with HTTPS enabled
- Configure executor to connect via HTTPS with CA validation

### Deploy Without TLS (Development Only)

```bash
TLS_MODE=none ./deploy-test.sh
```

⚠️ **Warning**: This disables all TLS encryption. Use only for local development.

## Certificate Management

### Automatic Certificate Handling

The deployment script intelligently manages certificates:

1. **First Deployment**: Creates new certificates automatically
2. **Subsequent Deployments**: Reuses existing valid certificates
3. **Near Expiry**: cert-manager automatically renews certificates (30 days before expiry)
4. **Certificate Issues**: Automatically detected and reported

### Manual Certificate Operations

#### Force Regenerate Certificates

```bash
FORCE_CERT_REGEN=true ./deploy-test.sh
```

Use this when you need to:
- Update certificate configuration
- Resolve certificate issues
- Test certificate rotation

#### Quick Redeploy Without Certificate Checks

```bash
SKIP_CERT_CHECK=true ./deploy-test.sh
```

This skips all certificate operations for faster deployment when you know certificates are already configured correctly.

#### Combine Options

```bash
# Force new certs and skip tests
FORCE_CERT_REGEN=true RUN_TESTS=false ./deploy-test.sh

# Quick redeploy without certs or tests  
SKIP_CERT_CHECK=true RUN_TESTS=false ./deploy-test.sh
```

## TLS Modes Configuration

### Internal Mode (Default)

Used for development and testing with self-signed certificates.

**values.yaml / test-values.yaml:**
```yaml
tls:
  enabled: true
  mode: "internal"
  
  internal:
    serviceName: "kubently-api.kubently.svc.cluster.local"
    duration: "8760h"    # 1 year
    renewBefore: "720h"  # Renew 30 days before expiry
```

**How it works:**
1. Creates self-signed CA certificate
2. Issues certificate for internal service names
3. Ingress terminates TLS for external access
4. **Executor connects via HTTP internally** (pod-to-service communication)
5. TLS is used for external access through ingress

**Architecture Note**: In internal mode, TLS is handled at the ingress level for external access. Pod-to-pod communication within the cluster uses HTTP directly to the service, as the ingress controller doesn't route internal cluster traffic.

### External Mode (Production)

Used for production deployments with public certificates.

**production-values.yaml:**
```yaml
tls:
  enabled: true
  mode: "external"
  
  external:
    domain: "api.kubently.example.com"  # Your public domain
    issuer: "letsencrypt-prod"          # Your ClusterIssuer
    duration: "2160h"    # 90 days
    renewBefore: "720h"  # Renew 30 days before expiry

# Also configure ingress for external access
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: api.kubently.example.com
      paths:
        - path: /
          pathType: Prefix
```

**Deploy to production:**
```bash
helm upgrade --install kubently ./deployment/helm/kubently \
  -f ./deployment/helm/production-values.yaml \
  --set tls.external.domain=$KUBENTLY_DOMAIN
```

**How it works:**
1. Requests certificate from Let's Encrypt (or your CA)
2. Certificate issued for public domain
3. Executor connects using public domain name
4. No CA mounting needed (uses system trust store)

### None Mode (HTTP Only)

Disables TLS completely. Not recommended for production.

```bash
TLS_MODE=none ./deploy-test.sh
```

Or in values:
```yaml
tls:
  enabled: false
  # or
  mode: "none"
```

## Certificate Lifecycle

### Development (Internal Mode)

1. **Initial Creation**: Self-signed CA and certificate created
2. **Validity**: 1 year by default
3. **Auto-renewal**: 30 days before expiry
4. **Manual renewal**: Use `FORCE_CERT_REGEN=true`

### Production (External Mode)

1. **Initial Creation**: Certificate requested from CA (e.g., Let's Encrypt)
2. **Validity**: 90 days (Let's Encrypt default)
3. **Auto-renewal**: 30 days before expiry
4. **DNS Validation**: Ensure domain points to cluster

## Troubleshooting

### Check Certificate Status

```bash
# View all certificates
kubectl get certificate -n kubently

# Detailed certificate information
kubectl describe certificate kubently-api-tls -n kubently

# Check if certificate is ready
kubectl get certificate kubently-api-tls -n kubently -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}'
```

### View Certificate Details

```bash
# Extract and decode certificate
kubectl get secret kubently-api-tls -n kubently -o yaml | \
  grep tls.crt | cut -d' ' -f4 | base64 -d | \
  openssl x509 -text -noout
```

### Check Ingress Configuration

```bash
# View ingress
kubectl get ingress -n kubently

# Detailed ingress info
kubectl describe ingress kubently -n kubently
```

### Verify TLS Connection

```bash
# Test HTTPS endpoint (internal mode)
kubectl run test-curl --rm -it --image=curlimages/curl -- \
  curl -v https://kubently-api.kubently.svc.cluster.local

# Check executor logs for TLS errors
kubectl logs -n kubently -l app.kubernetes.io/component=executor | grep -i tls
```

### Common Issues

#### Certificate Not Ready

**Symptom**: Certificate shows as not ready after deployment

**Solution**:
```bash
# Check cert-manager logs
kubectl logs -n cert-manager deployment/cert-manager

# Check certificate events
kubectl describe certificate kubently-api-tls -n kubently

# Force regeneration if needed
FORCE_CERT_REGEN=true ./deploy-test.sh
```

#### Executor Cannot Connect

**Symptom**: Executor fails to connect to API with TLS errors

**Solution**:
```bash
# Check executor environment
kubectl describe pod -n kubently -l app.kubernetes.io/component=executor

# Verify CA certificate is mounted (internal mode)
kubectl exec -n kubently -it <executor-pod> -- ls /etc/kubently/ca/

# Check executor logs
kubectl logs -n kubently -l app.kubernetes.io/component=executor
```

#### Ingress Not Working

**Symptom**: Cannot access API via HTTPS

**Solution**:
```bash
# Ensure nginx ingress controller is running
kubectl get pods -n ingress-nginx

# Check ingress logs
kubectl logs -n ingress-nginx deployment/ingress-nginx-controller

# Verify ingress configuration
kubectl get ingress kubently -n kubently -o yaml
```

## Security Best Practices

1. **Always use TLS in production** - Never deploy with `TLS_MODE=none` in production
2. **Use public certificates for external access** - Let's Encrypt or commercial CA
3. **Monitor certificate expiry** - Set up alerts for certificate renewal
4. **Rotate certificates regularly** - Don't wait for expiry
5. **Secure private keys** - Ensure proper RBAC for certificate secrets
6. **Use strong cipher suites** - Configure in ingress annotations

## Migration from HTTP to HTTPS

If you have an existing HTTP deployment:

1. **Backup your configuration**
   ```bash
   kubectl get all -n kubently -o yaml > backup.yaml
   ```

2. **Deploy with TLS enabled**
   ```bash
   ./deploy-test.sh  # Uses TLS by default
   ```

3. **Verify executor reconnects**
   ```bash
   kubectl logs -n kubently -l app.kubernetes.io/component=executor
   ```

4. **Update any external integrations** to use HTTPS endpoints

## Environment Variables

The executor supports these TLS-related environment variables:

- `KUBENTLY_API_URL` - Automatically set based on TLS mode
- `KUBENTLY_SSL_VERIFY` - Set to "true" when TLS is enabled
- `KUBENTLY_CA_CERT` - Path to CA certificate (internal mode only)

These are configured automatically by the Helm chart based on your TLS settings.

## Testing TLS Configuration

After deployment, verify TLS is working:

```bash
# Check certificate status
kubectl get certificate -n kubently

# Test executor connection
kubectl logs -n kubently -l app.kubernetes.io/component=executor | grep "Using HTTPS"

# Run automated tests
bash test-a2a.sh
```

## Advanced Configuration

### Custom Certificate Authority

If using your own CA:

1. Create a secret with your CA certificate
2. Reference it in values:
   ```yaml
   tls:
     internal:
       issuer: "my-ca-issuer"  # Your CA issuer
   ```

### Multiple Domains

For multiple domains in external mode:

```yaml
tls:
  external:
    domain: "api.kubently.com"
    additionalDomains:
      - "api-backup.kubently.com"
      - "*.kubently.com"
```

### Certificate Pinning

For enhanced security, pin certificates in executor:

```yaml
executor:
  env:
    KUBENTLY_CERT_FINGERPRINT: "sha256:..."
```

## Summary

The TLS implementation provides:

- ✅ **Secure by default** - TLS enabled automatically
- ✅ **Flexible deployment** - Support for different environments
- ✅ **Automatic management** - Certificates handled by cert-manager
- ✅ **Easy override** - Can disable when needed for development
- ✅ **Production ready** - Full support for public certificates
- ✅ **Zero API changes** - TLS termination at ingress level