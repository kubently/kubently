#!/bin/bash
# Deploy Kubently using Helm with all necessary configurations

set -e

NAMESPACE=${NAMESPACE:-kubently}
RELEASE_NAME=${RELEASE_NAME:-kubently}
VALUES_FILE=${VALUES_FILE:-deployment/helm/test-values.yaml}

echo "🚀 Deploying Kubently with Helm..."
echo "   Namespace: $NAMESPACE"
echo "   Release: $RELEASE_NAME"
echo "   Values: $VALUES_FILE"

# Create namespace if it doesn't exist
echo "📦 Creating namespace..."
kubectl create namespace $NAMESPACE --dry-run=client -o yaml | kubectl apply -f -

# Create LLM API key secret
echo "🔐 Creating LLM API key secret..."
./deployment/scripts/create-llm-secret.sh

# Build and load Docker images
echo "🐳 Building Docker images..."
docker build -t kubently/api:latest -f deployment/docker/api/Dockerfile .
docker build -t kubently/executor:latest -f deployment/docker/executor/Dockerfile .

echo "📤 Loading images into kind cluster..."
kind load docker-image kubently/api:latest --name kubently
kind load docker-image kubently/executor:latest --name kubently

# Deploy with Helm
echo "⚙️ Installing/upgrading Helm release..."
helm upgrade --install $RELEASE_NAME \
  deployment/helm/kubently \
  --namespace $NAMESPACE \
  --values $VALUES_FILE \
  --wait \
  --timeout 5m

echo "✅ Deployment complete!"
echo ""
echo "Check status with:"
echo "  kubectl get pods -n $NAMESPACE"
echo "  kubectl logs -n $NAMESPACE -l app.kubernetes.io/name=kubently"