# Authentication Discovery

## Overview

The Kubently API now provides an authentication discovery endpoint that allows clients to automatically determine:
- Which authentication methods are supported
- OIDC provider configuration (if OAuth is enabled)
- Required headers and parameters

This eliminates the need for clients to manually configure OIDC settings.

## Discovery Endpoint

### `GET /.well-known/kubently-auth`

Returns the authentication configuration for the Kubently instance.

**Example Response (OAuth Enabled):**
```json
{
  "authentication_methods": ["api_key", "oauth"],
  "api_key": {
    "header": "X-API-Key",
    "description": "Static API key for service authentication"
  },
  "oauth": {
    "enabled": true,
    "issuer": "https://auth.example.com",
    "client_id": "kubently-cli",
    "device_authorization_endpoint": "https://auth.example.com/device/code",
    "token_endpoint": "https://auth.example.com/token",
    "grant_types": ["urn:ietf:params:oauth:grant-type:device_code"],
    "response_types": ["token", "id_token"],
    "scopes": ["openid", "email", "profile", "groups"]
  }
}
```

**Example Response (OAuth Disabled):**
```json
{
  "authentication_methods": ["api_key"],
  "api_key": {
    "header": "X-API-Key",
    "description": "Static API key for service authentication"
  },
  "oauth": {
    "enabled": false,
    "message": "OAuth authentication is not configured for this instance"
  }
}
```

## CLI Auto-Discovery

The `kubently login` command now automatically discovers OIDC configuration:

### Discovery Flow

1. **Check API Discovery**: Query `/.well-known/kubently-auth`
2. **Use Discovered Config**: If OAuth is enabled, use the issuer and client_id
3. **Handle Disabled OAuth**: If OAuth is disabled, suggest API key authentication
4. **Fallback Chain**: 
   - Environment variables (`OIDC_ISSUER`, `OIDC_CLIENT_ID`)
   - Saved configuration
   - Default values

### Usage Examples

**Auto-discovery (default):**
```bash
# CLI will discover OIDC config from API
kubently login
```

**Skip discovery:**
```bash
# Use explicit configuration
kubently login --issuer https://auth.example.com --client-id my-client

# Disable discovery entirely
kubently login --no-discovery
```

**Specify API URL for discovery:**
```bash
# Use a specific API for discovery
kubently login --api-url https://kubently.example.com
```

## Server Configuration

Configure the Kubently API to advertise OAuth settings:

### Environment Variables
```bash
# Enable OAuth discovery
OIDC_ENABLED=true
OIDC_ISSUER=https://auth.example.com
OIDC_CLIENT_ID=kubently-cli

# Optional: Override endpoint URLs
OIDC_DEVICE_AUTH_ENDPOINT=https://auth.example.com/device/code
OIDC_TOKEN_ENDPOINT=https://auth.example.com/token
```

### Kubernetes ConfigMap
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: kubently-env
  namespace: kubently
data:
  OIDC_ENABLED: "true"
  OIDC_ISSUER: "https://auth.example.com"
  OIDC_CLIENT_ID: "kubently-cli"
```

### Helm Values
```yaml
api:
  oidc:
    enabled: true
    issuer: "https://auth.example.com"
    clientId: "kubently-cli"
    jwksUri: "https://auth.example.com/jwks"
    audience: "kubently-cli"
```

## Benefits

1. **Zero Configuration**: Clients don't need to know OIDC details
2. **Dynamic Discovery**: Configuration changes are immediately reflected
3. **Graceful Degradation**: Falls back to API keys if OAuth unavailable
4. **Multi-Environment**: Different configurations per deployment
5. **User Experience**: Simpler onboarding for new users

## Testing Discovery

Test the discovery endpoint:
```bash
# Check what authentication is available
curl https://kubently.example.com/.well-known/kubently-auth | jq .

# Test with the provided script
./test-auth-discovery.sh
```

## Security Considerations

- Discovery endpoint is **unauthenticated** (by design)
- Only reveals public configuration (issuer, client_id)
- Does not expose secrets or internal configuration
- Clients still validate JWTs against JWKS endpoint

## Migration Guide

### For Existing Deployments

1. **Add OIDC configuration** to environment/ConfigMap
2. **Deploy updated API** with discovery endpoint
3. **Update CLI** to latest version
4. **Users run** `kubently login` - auto-discovers configuration

### For New Deployments

1. **Configure OIDC** in Helm values or environment
2. **Deploy Kubently** with OAuth enabled
3. **Users run** `kubently login` - everything auto-configures

## Troubleshooting

### Discovery Not Working

```bash
# Check if endpoint is available
curl -v http://localhost:8080/.well-known/kubently-auth

# Check API logs
kubectl logs -n kubently deployment/kubently-api

# Verify environment variables
kubectl describe configmap -n kubently kubently-env
```

### OAuth Shows as Disabled

Ensure these environment variables are set:
- `OIDC_ENABLED=true`
- `OIDC_ISSUER` (must be non-empty)

### Wrong OIDC Provider

Check configuration precedence:
1. CLI flags (`--issuer`, `--client-id`)
2. Discovery from API
3. Environment variables
4. Saved configuration
5. Default values