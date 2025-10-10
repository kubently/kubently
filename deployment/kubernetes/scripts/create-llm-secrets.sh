#!/bin/bash

# Script to create Kubernetes secrets for LLM API keys
# Usage: ./create-llm-secrets.sh [namespace]

set -e

NAMESPACE="${1:-kubently}"

# Create namespace if it doesn't exist
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

echo "Creating LLM API key secrets in namespace '$NAMESPACE'"
echo ""
echo "This script will help you create secrets for LLM providers."
echo "Leave blank if you don't want to set a particular key."
echo ""

# Read API keys (don't echo the input)
read -sp "Enter OpenAI API Key (or press Enter to skip): " OPENAI_KEY
echo ""
read -sp "Enter Anthropic API Key (or press Enter to skip): " ANTHROPIC_KEY
echo ""

# Delete existing secret if it exists
kubectl delete secret llm-api-keys -n "$NAMESPACE" 2>/dev/null || true

# Build the secret command
SECRET_CMD="kubectl create secret generic llm-api-keys -n $NAMESPACE"

if [ -n "$OPENAI_KEY" ]; then
    SECRET_CMD="$SECRET_CMD --from-literal=openai-key=$OPENAI_KEY"
fi

if [ -n "$ANTHROPIC_KEY" ]; then
    SECRET_CMD="$SECRET_CMD --from-literal=anthropic-key=$ANTHROPIC_KEY"
fi

# Only create secret if at least one key was provided
if [ -n "$OPENAI_KEY" ] || [ -n "$ANTHROPIC_KEY" ]; then
    eval $SECRET_CMD
    echo "Secret 'llm-api-keys' created successfully"
else
    echo "No API keys provided, skipping secret creation"
fi