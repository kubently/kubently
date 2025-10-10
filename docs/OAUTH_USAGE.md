# OAuth 2.0 Authentication Usage Guide

## Overview
Kubently now supports dual authentication:
- **OAuth 2.0 / OIDC** for human users (via `kubently login`)
- **API Keys** for services and legacy support

## Quick Start

### Using API Key Authentication (Legacy/Services)
```bash
# Configure with API key
kubently login --use-api-key <your-api-key>

# Example
kubently login --use-api-key test-api-key
```

### Using OAuth Authentication (Human Users)
```bash
# Start the mock OAuth provider (for testing)
python3 -m venv /tmp/oauth-venv
source /tmp/oauth-venv/bin/activate
pip install PyJWT cryptography fastapi uvicorn httpx
python3 kubently/modules/auth/mock_oauth_provider.py

# In another terminal, login with OAuth
kubently login

# Follow the device authorization flow:
# 1. Note the user code displayed
# 2. Visit http://localhost:9000/device
# 3. Enter the user code
# 4. Select a test user (test@example.com or admin@example.com)
# 5. CLI will automatically complete authentication
```

## Configuration

The authentication configuration is stored in `~/.kubently/config.json`:

```json
{
  "authMethod": "api_key",  // or "oauth"
  "apiKey": "test-api-key",  // for API key auth
  "oauthTokens": {           // for OAuth auth
    "access_token": "...",
    "refresh_token": "...",
    "id_token": "...",
    "expires_at": 1234567890
  }
}
```

## Command Options

### kubently login
- `--issuer <url>` - OIDC issuer URL (default: http://localhost:9000)
- `--client-id <id>` - OAuth client ID (default: kubently-cli)
- `--no-browser` - Don't auto-open browser for device auth
- `--use-api-key <key>` - Use API key instead of OAuth

## Testing

### Test API Key Authentication
```bash
kubently login --use-api-key test-api-key
kubently debug  # Should work with API key
```

### Test OAuth Flow (with mock provider)
```bash
# Terminal 1: Start mock OAuth provider
./run-mock-oauth.sh  # Or use the venv method above

# Terminal 2: Login
kubently login

# Terminal 3: Test authenticated access
kubently debug
```

## Production Setup

For production, configure real OIDC provider:

1. Set environment variables:
```bash
export OIDC_ISSUER=https://your-provider.com
export OIDC_CLIENT_ID=your-client-id
export OIDC_JWKS_URI=https://your-provider.com/jwks
```

2. Update Kubernetes deployment:
```yaml
# deployment/k8s/configmap-env.yaml
data:
  OIDC_ENABLED: "true"
  OIDC_ISSUER: "https://your-provider.com"
  OIDC_CLIENT_ID: "your-client-id"
  OIDC_JWKS_URI: "https://your-provider.com/jwks"
```

## Dual Authentication in API

The Kubently API now accepts both:
- `X-API-Key: <key>` header (for services)
- `Authorization: Bearer <jwt>` header (for OAuth users)

Both authentication methods work simultaneously, allowing gradual migration.

## Troubleshooting

### "kubently login" shows help instead of running
Make sure you have the latest version:
```bash
cd kubently-cli/nodejs
npm run build
npm link  # Updates global kubently command
```

### OAuth provider not starting
Install dependencies in a virtual environment:
```bash
python3 -m venv /tmp/oauth-venv
source /tmp/oauth-venv/bin/activate
pip install PyJWT cryptography fastapi uvicorn httpx
python3 kubently/modules/auth/mock_oauth_provider.py
```

### Token expired
Re-authenticate with:
```bash
kubently login
```

## Security Notes

- OAuth tokens are stored in `~/.kubently/config.json` with restricted permissions (0600)
- Tokens expire after 1 hour (configurable)
- API keys continue to work for backward compatibility
- JWT tokens are validated against the OIDC provider's public keys