#!/bin/bash
# Generate Redis password and create Kubernetes secret

set -e

NAMESPACE=${NAMESPACE:-kubently}
SECRETS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASSWORD_FILE="$SECRETS_DIR/redis-password.txt"

# Generate password if it doesn't exist
if [ ! -f "$PASSWORD_FILE" ]; then
    echo "Generating new Redis password..."
    openssl rand -base64 32 > "$PASSWORD_FILE"
    chmod 600 "$PASSWORD_FILE"
    echo "✓ Password generated and saved to $PASSWORD_FILE"
else
    echo "Using existing password from $PASSWORD_FILE"
fi

# Read password
PASSWORD=$(cat "$PASSWORD_FILE")

# Create or update Kubernetes secret
echo "Creating/updating Kubernetes secret..."
kubectl create secret generic kubently-redis-password \
    --from-literal=password="$PASSWORD" \
    --namespace="$NAMESPACE" \
    --dry-run=client -o yaml | kubectl apply -f -

echo "✓ Secret 'kubently-redis-password' created/updated in namespace '$NAMESPACE'"
echo ""
echo "Password file location: $PASSWORD_FILE"
echo "Keep this file secure and never commit it to git!"
