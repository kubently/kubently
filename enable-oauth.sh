#!/bin/bash

# Script to enable OAuth in Kubently deployment

echo "🔐 Enabling OAuth for Kubently"
echo "=============================="
echo ""

# Check if mock OAuth provider should be used
read -p "Use mock OAuth provider for testing? (y/n): " use_mock

if [ "$use_mock" = "y" ]; then
    OIDC_ISSUER="http://localhost:9000"
    echo "Using mock OAuth provider at $OIDC_ISSUER"
    
    # Start mock provider if not running
    if ! curl -s http://localhost:9000/.well-known/openid-configuration > /dev/null 2>&1; then
        echo "Starting mock OAuth provider..."
        echo "Run this in a separate terminal:"
        echo ""
        echo "  python3 -m venv /tmp/oauth-venv"
        echo "  source /tmp/oauth-venv/bin/activate"
        echo "  pip install PyJWT cryptography fastapi uvicorn httpx"
        echo "  python3 kubently/modules/auth/mock_oauth_provider.py"
        echo ""
        read -p "Press Enter when mock provider is running..."
    fi
else
    read -p "Enter OIDC Issuer URL (e.g., https://auth.example.com): " OIDC_ISSUER
fi

read -p "Enter Client ID (default: kubently-cli): " CLIENT_ID
CLIENT_ID=${CLIENT_ID:-kubently-cli}

echo ""
echo "Updating Kubernetes ConfigMap..."

# Create or update the ConfigMap
cat <<EOF | kubectl apply -n kubently -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: kubently-env
  namespace: kubently
data:
  OIDC_ENABLED: "true"
  OIDC_ISSUER: "$OIDC_ISSUER"
  OIDC_CLIENT_ID: "$CLIENT_ID"
  OIDC_AUDIENCE: "$CLIENT_ID"
  OIDC_JWKS_URI: "$OIDC_ISSUER/jwks"
  # Keep existing config
  LLM_PROVIDER: "openai"
  OPENAI_ENDPOINT: "https://api.openai.com/v1"
  OPENAI_MODEL_NAME: "gpt-4o"
  A2A_SERVER_DEBUG: "true"
  API_PORT: "8080"
EOF

echo ""
echo "Restarting Kubently API to apply changes..."
kubectl rollout restart deployment/kubently-api -n kubently

echo ""
echo "Waiting for rollout to complete..."
kubectl rollout status deployment/kubently-api -n kubently

echo ""
echo "✅ OAuth has been enabled!"
echo ""
echo "Test the configuration:"
echo "  1. Check discovery: curl http://localhost:8080/.well-known/kubently-auth | jq ."
echo "  2. Login with OAuth: kubently login"
echo "  3. Or use API key: kubently login --use-api-key test-api-key"