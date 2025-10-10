#!/bin/bash
# Script to create LLM API key secret from environment or .env file
# This keeps sensitive API keys out of Helm values

set -e

NAMESPACE=${NAMESPACE:-kubently}

# Source .env file if it exists
if [ -f .env ]; then
    echo "Loading API key from .env file..."
    export $(grep -E "^OPENAI_API_KEY=" .env | xargs)
fi

# Check if OPENAI_API_KEY is set
if [ -z "$OPENAI_API_KEY" ]; then
    echo "Error: OPENAI_API_KEY not set in environment or .env file"
    echo "Please set OPENAI_API_KEY environment variable or create a .env file"
    exit 1
fi

echo "Creating LLM API key secret in namespace $NAMESPACE..."

# Create the secret
kubectl create secret generic llm-api-keys \
    --from-literal=openai-key="$OPENAI_API_KEY" \
    --namespace="$NAMESPACE" \
    --dry-run=client -o yaml | kubectl apply -f -

echo "Secret 'llm-api-keys' created/updated successfully"