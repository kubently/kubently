#!/bin/bash

# Script to generate a Kubernetes ConfigMap from .env file
# Usage: ./generate-configmap-from-env.sh [env-file] [namespace]

set -e

ENV_FILE="${1:-.env}"
NAMESPACE="${2:-kubently}"
CONFIGMAP_NAME="kubently-env-config"

# Check if env file exists
if [ ! -f "$ENV_FILE" ]; then
    echo "Error: Environment file '$ENV_FILE' not found"
    exit 1
fi

echo "Generating ConfigMap from $ENV_FILE..."

# Create namespace if it doesn't exist
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

# Delete existing ConfigMap if it exists
kubectl delete configmap "$CONFIGMAP_NAME" -n "$NAMESPACE" 2>/dev/null || true

# Create ConfigMap from env file
# This will automatically handle the .env format
kubectl create configmap "$CONFIGMAP_NAME" \
    --from-env-file="$ENV_FILE" \
    -n "$NAMESPACE"

echo "ConfigMap '$CONFIGMAP_NAME' created in namespace '$NAMESPACE'"

# Show the created ConfigMap
echo ""
echo "ConfigMap contents:"
kubectl get configmap "$CONFIGMAP_NAME" -n "$NAMESPACE" -o yaml