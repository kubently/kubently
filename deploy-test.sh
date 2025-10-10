#!/bin/bash
# Deployment script for Kubently with test values
# This ensures consistent deployment with all required configuration
#
# Usage:
#   ./deploy-test.sh                        # Normal deployment with TLS
#   FORCE_CERT_REGEN=true ./deploy-test.sh  # Force new certificates
#   TLS_MODE=none ./deploy-test.sh          # Deploy without TLS (HTTP only)
#   SKIP_CERT_CHECK=true ./deploy-test.sh   # Quick redeploy without cert checks
#
# Environment variables:
#   TLS_MODE           - Set TLS mode: internal (default), external, or none
#   FORCE_CERT_REGEN   - Force regeneration of certificates (default: false)
#   SKIP_CERT_CHECK    - Skip all certificate checks (default: false)
#   RUN_TESTS          - Run automated tests after deployment (default: true)
#   FORCE_CLEANUP      - Force cleanup of existing resources before deploy (default: false)

set -e

NAMESPACE=${NAMESPACE:-kubently}
RELEASE_NAME=${RELEASE_NAME:-kubently}
VALUES_FILE=${VALUES_FILE:-deployment/helm/test-values.yaml}
KUBE_CONTEXT="kind-kubently"

# TLS Configuration
FORCE_CERT_REGEN=${FORCE_CERT_REGEN:-false}
SKIP_CERT_CHECK=${SKIP_CERT_CHECK:-false}
TLS_MODE=${TLS_MODE:-internal}  # internal, external, or none
FORCE_CLEANUP=${FORCE_CLEANUP:-false}  # Force cleanup of existing resources

echo "🚀 Deploying Kubently with test configuration..."
echo "   Context: $KUBE_CONTEXT"
echo "   Namespace: $NAMESPACE"
echo "   Release: $RELEASE_NAME"
echo "   Values: $VALUES_FILE"
echo "   TLS Mode: $TLS_MODE"

# Verify the context exists
if ! kubectl config get-contexts $KUBE_CONTEXT &>/dev/null; then
    echo "❌ Error: Kubernetes context '$KUBE_CONTEXT' not found"
    echo "   Please create the kind cluster first: kind create cluster --name kubently"
    exit 1
fi

# Create namespace if it doesn't exist
echo "📦 Creating namespace..."
kubectl --context=$KUBE_CONTEXT create namespace $NAMESPACE --dry-run=client -o yaml | kubectl --context=$KUBE_CONTEXT apply -f -

# Function to check if certificates exist and are valid
check_certificates() {
    echo "🔍 Checking TLS certificates..."
    
    if [ "$TLS_MODE" = "none" ]; then
        echo "ℹ️  TLS disabled, skipping certificate checks"
        return 0
    fi
    
    # Check if cert-manager is installed
    if ! kubectl --context=$KUBE_CONTEXT get deployment -n cert-manager cert-manager &>/dev/null; then
        echo "⚠️  cert-manager not found. Installing..."
        install_cert_manager
        return 1  # Force cert generation after install
    fi
    
    # Check if certificate already exists
    if kubectl --context=$KUBE_CONTEXT get certificate kubently-api-tls -n $NAMESPACE &>/dev/null; then
        # Check certificate status
        CERT_READY=$(kubectl --context=$KUBE_CONTEXT get certificate kubently-api-tls -n $NAMESPACE -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
        
        if [ "$CERT_READY" = "True" ]; then
            echo "✅ Certificate exists and is ready"
            
            # Check if force regeneration is requested
            if [ "$FORCE_CERT_REGEN" = "true" ]; then
                echo "🔄 Force regeneration requested..."
                kubectl --context=$KUBE_CONTEXT delete certificate kubently-api-tls -n $NAMESPACE
                return 1
            fi
            return 0
        else
            echo "⚠️  Certificate exists but is not ready"
            return 1
        fi
    else
        echo "ℹ️  No certificate found, will create new one"
        return 1
    fi
}

# Function to install cert-manager
install_cert_manager() {
    echo "📦 Installing cert-manager..."
    kubectl --context=$KUBE_CONTEXT apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.13.0/cert-manager.yaml
    
    # Wait for cert-manager to be ready
    echo "⏳ Waiting for cert-manager to be ready..."
    kubectl --context=$KUBE_CONTEXT wait --for=condition=Available --timeout=300s \
        deployment/cert-manager -n cert-manager
    kubectl --context=$KUBE_CONTEXT wait --for=condition=Available --timeout=300s \
        deployment/cert-manager-webhook -n cert-manager
}

# Function to setup ingress controller
setup_ingress() {
    if [ "$TLS_MODE" = "none" ]; then
        echo "ℹ️  TLS disabled, skipping ingress setup"
        return 0
    fi
    
    echo "🔍 Checking nginx ingress controller..."
    if ! kubectl --context=$KUBE_CONTEXT get deployment -n ingress-nginx ingress-nginx-controller &>/dev/null; then
        echo "📦 Installing nginx ingress controller for kind..."
        kubectl --context=$KUBE_CONTEXT apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
        
        echo "⏳ Waiting for ingress controller to be ready..."
        kubectl --context=$KUBE_CONTEXT wait --for=condition=ready pod \
            -l app.kubernetes.io/component=controller \
            -n ingress-nginx --timeout=300s
    else
        echo "✅ Nginx ingress controller already installed"
    fi
}

# Setup TLS prerequisites
if [ "$SKIP_CERT_CHECK" != "true" ]; then
    setup_ingress
    
    # Check/install certificates
    if ! check_certificates; then
        echo "📝 Certificates need to be generated/regenerated"
        # Certificates will be created by Helm chart with cert-manager
    fi
else
    echo "⚠️  Skipping certificate checks (SKIP_CERT_CHECK=true)"
fi

# Create LLM API key secret from .env file
# Note: This secret is created outside of Helm because it contains sensitive API keys
# that should not be stored in values files
echo "🔐 Creating LLM API key secret..."
if [ -f .env ]; then
    # Load all LLM API keys using source to handle special characters properly
    set -a  # Enable automatic export of variables
    source .env
    set +a  # Disable automatic export
fi

# Create secret with all available LLM API keys
SECRET_ARGS=""
SECRET_CREATED=false

if [ -n "$GOOGLE_API_KEY" ]; then
    SECRET_ARGS="$SECRET_ARGS --from-literal=google-key=$GOOGLE_API_KEY"
    echo "📝 Found Google API key for Gemini..."
    SECRET_CREATED=true
fi

if [ -n "$OPENAI_API_KEY" ]; then
    SECRET_ARGS="$SECRET_ARGS --from-literal=openai-key=$OPENAI_API_KEY"
    echo "📝 Found OpenAI API key..."
    SECRET_CREATED=true
fi

if [ -n "$ANTHROPIC_API_KEY" ]; then
    SECRET_ARGS="$SECRET_ARGS --from-literal=anthropic-key=$ANTHROPIC_API_KEY"
    echo "📝 Found Anthropic API key..."
    SECRET_CREATED=true
fi

if [ "$SECRET_CREATED" = "true" ]; then
    echo "📝 Creating LLM secret with available API keys..."
    # Delete existing secret if it exists (to ensure clean state)
    kubectl --context=$KUBE_CONTEXT delete secret llm-api-keys -n "$NAMESPACE" 2>/dev/null || true
    
    # Create the secret with labels
    kubectl --context=$KUBE_CONTEXT create secret generic llm-api-keys \
        $SECRET_ARGS \
        --namespace="$NAMESPACE"
    
    # Add label to mark as managed by deploy script
    kubectl --context=$KUBE_CONTEXT label secret llm-api-keys \
        "app.kubernetes.io/managed-by=deploy-script" \
        --namespace="$NAMESPACE" \
        --overwrite
    
    echo "✅ Secret 'llm-api-keys' created/updated successfully with LLM API keys"
else
    echo "⚠️ Warning: No LLM API keys (GOOGLE_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY) found in environment or .env file"
    echo "A2A agent will not function without an LLM API key"
fi

# Generate unique tag based on timestamp
TAG=$(date +%Y%m%d-%H%M%S)
echo "🏷️ Using image tag: $TAG"

# Sync prompts to Helm chart before building
echo "📝 Syncing prompts to Helm chart..."
cp prompts/*.yaml deployment/helm/kubently/prompts/ 2>/dev/null || true

# Build and load images with unique tag
echo "📦 Building Docker images..."
docker build -f deployment/docker/api/Dockerfile -t kubently/api:$TAG -t kubently/api:latest . || exit 1
docker build -f deployment/docker/executor/Dockerfile -t kubently/executor:$TAG -t kubently/executor:latest . || exit 1

echo "📤 Loading images into kind cluster..."
kind load docker-image kubently/api:$TAG --name kubently
kind load docker-image kubently/executor:$TAG --name kubently

# Check for any Helm release issues
HELM_STATUS=$(helm list -n $NAMESPACE --kube-context=$KUBE_CONTEXT 2>/dev/null | grep "$RELEASE_NAME" | awk '{print $4}' || echo "")

# Clean up if we have a failed/pending release or force cleanup is requested
if [ "$FORCE_CLEANUP" = "true" ] || [ "$HELM_STATUS" = "failed" ] || [ "$HELM_STATUS" = "pending-install" ] || [ "$HELM_STATUS" = "pending-upgrade" ]; then
    echo "🧹 Cleaning up Helm release and resources..."
    # Try to rollback first if pending
    if [[ "$HELM_STATUS" =~ "pending" ]]; then
        helm rollback $RELEASE_NAME -n $NAMESPACE --kube-context=$KUBE_CONTEXT 2>/dev/null || true
    fi
    # Uninstall the release
    helm uninstall $RELEASE_NAME -n $NAMESPACE --kube-context=$KUBE_CONTEXT 2>/dev/null || true
fi

# Always clean up potential conflicting resources that might exist outside Helm
echo "🔍 Checking for conflicting resources..."
CONFLICTS_FOUND=false

# Check and remove conflicting resources
if kubectl get serviceaccount kubently-executor -n $NAMESPACE --kube-context=$KUBE_CONTEXT &>/dev/null; then
    # Only delete if not owned by current Helm release
    if ! kubectl get serviceaccount kubently-executor -n $NAMESPACE --kube-context=$KUBE_CONTEXT -o json | grep -q "meta.helm.sh/release-name"; then
        echo "  - Removing orphaned serviceaccount kubently-executor"
        kubectl delete serviceaccount kubently-executor -n $NAMESPACE --kube-context=$KUBE_CONTEXT
        CONFLICTS_FOUND=true
    fi
fi

if kubectl get secret kubently-api-keys -n $NAMESPACE --kube-context=$KUBE_CONTEXT &>/dev/null; then
    # Only delete if not owned by current Helm release
    if ! kubectl get secret kubently-api-keys -n $NAMESPACE --kube-context=$KUBE_CONTEXT -o json | grep -q "meta.helm.sh/release-name"; then
        echo "  - Removing orphaned secret kubently-api-keys"
        kubectl delete secret kubently-api-keys -n $NAMESPACE --kube-context=$KUBE_CONTEXT
        CONFLICTS_FOUND=true
    fi
fi

if kubectl get certificate kubently-api-tls -n $NAMESPACE --kube-context=$KUBE_CONTEXT &>/dev/null; then
    # Only delete if not owned by current Helm release
    if ! kubectl get certificate kubently-api-tls -n $NAMESPACE --kube-context=$KUBE_CONTEXT -o json | grep -q "meta.helm.sh/release-name"; then
        echo "  - Removing orphaned certificate kubently-api-tls"
        kubectl delete certificate kubently-api-tls -n $NAMESPACE --kube-context=$KUBE_CONTEXT
        CONFLICTS_FOUND=true
    fi
fi

if [ "$CONFLICTS_FOUND" = "false" ]; then
    echo "✅ No conflicting resources found"
fi

# Deploy with Helm using the new tag
echo "⚙️ Deploying with Helm (TLS mode: $TLS_MODE)..."
# Use --force to handle any lingering resources
helm upgrade --install $RELEASE_NAME ./deployment/helm/kubently \
  --kube-context=$KUBE_CONTEXT \
  --namespace $NAMESPACE \
  --values $VALUES_FILE \
  --set api.image.tag=$TAG \
  --set executor.image.tag=$TAG \
  --set tls.mode=$TLS_MODE \
  --force \
  --wait \
  --timeout 5m

# Wait for certificate to be ready (if TLS enabled)
if [ "$TLS_MODE" != "none" ] && [ "$SKIP_CERT_CHECK" != "true" ]; then
    echo "⏳ Waiting for certificate to be ready..."
    kubectl --context=$KUBE_CONTEXT wait --for=condition=Ready certificate/kubently-api-tls \
        -n $NAMESPACE --timeout=120s || {
        echo "⚠️  Certificate not ready after 2 minutes, checking status..."
        kubectl --context=$KUBE_CONTEXT describe certificate kubently-api-tls -n $NAMESPACE
    }
fi

echo "✅ Deployment complete!"

# Show TLS status
if [ "$TLS_MODE" != "none" ]; then
    echo "🔐 TLS Status:"
    kubectl --context=$KUBE_CONTEXT get certificate -n $NAMESPACE 2>/dev/null || true
    kubectl --context=$KUBE_CONTEXT get ingress -n $NAMESPACE 2>/dev/null || true
fi

# Set up port forwarding
echo "🔌 Setting up port forwarding..."
# Kill only kubently-related port-forwards
pkill -f "port-forward.*kubently-api" 2>/dev/null || true
sleep 2

# Start port-forwards in background
kubectl --context=$KUBE_CONTEXT port-forward svc/kubently-api 8080:8080 -n $NAMESPACE > /dev/null 2>&1 &
PF_API=$!

# Wait for port-forwards to establish
sleep 3

# Verify port-forwards are working
echo "🔍 Verifying port-forwards..."
for port in 8080; do
    if ! nc -z localhost $port 2>/dev/null; then
        echo "❌ Error: Port $port is not accessible"
        echo "   Port-forward may have failed. Check kubectl logs."
        exit 1
    fi
done

echo "✨ Port forwarding established:"
echo "   - API/A2A endpoint: http://localhost:8080 (A2A at /a2a/)"

# Wait for A2A server to be ready
echo "⏳ Waiting for A2A server to be ready..."
MAX_ATTEMPTS=30
ATTEMPT=0
while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    if curl -s -X POST http://localhost:8080/a2a/ \
        -H "Content-Type: application/json" \
        -H "X-Api-Key: test-api-key" \
        -d '{"jsonrpc":"2.0","id":"health","method":"message/stream","params":{"message":{"role":"user","parts":[{"text":"test","partId":"test"}],"messageId":"test"}}}' \
        2>/dev/null | grep -q "jsonrpc"; then
        echo "✅ A2A server is ready!"
        break
    fi
    ATTEMPT=$((ATTEMPT + 1))
    if [ $ATTEMPT -eq $MAX_ATTEMPTS ]; then
        echo "❌ A2A server failed to respond after $MAX_ATTEMPTS attempts"
        echo "   Check logs: kubectl --context=$KUBE_CONTEXT logs -n $NAMESPACE -l app.kubernetes.io/component=api"
        exit 1
    fi
    sleep 2
done

# Store executor token in Redis for authentication
echo "🔑 Configuring executor authentication..."
kubectl --context=$KUBE_CONTEXT exec -n $NAMESPACE kubently-redis-master-0 -- redis-cli SET "executor:token:kind" "37d34f63b1d4a7420fceefc6eceb2c89d7124153f2a26578bcd2bc8082b538f0" > /dev/null
kubectl --context=$KUBE_CONTEXT exec -n $NAMESPACE kubently-redis-master-0 -- redis-cli SET "executor:token:kubently" "37d34f63b1d4a7420fceefc6eceb2c89d7124153f2a26578bcd2bc8082b538f0" > /dev/null

echo "🎉 Kubently is ready for testing!"

# Run automated tests if enabled (default: true)
if [ "${RUN_TESTS:-true}" = "true" ]; then
  echo ""
  echo "🧪 Running automated A2A tests..."
  if bash test-a2a.sh; then
    echo "✅ Automated tests completed successfully!"
  else
    echo "⚠️  Some tests failed. Check the output above for details."
    echo "You can skip tests with: RUN_TESTS=false ./deploy-test.sh"
  fi
fi

echo ""
echo "Test commands:"
echo "  # Quick test queries (see docs/TEST_QUERIES.md for more):"
echo "  bash test-a2a.sh"
echo ""
echo "  # Interactive A2A chat (recommended):"
echo "  kubently --api-url localhost:8080 --api-key test-api-key debug"
echo "  "
echo "  # Then in the session:"
echo "  # 1. Select cluster: 'use cluster kind'"
echo "  # 2. Run commands: 'kubectl get pods -n kubently'"
echo "  "
echo "  # Test with curl (requires x-api-key header):"
echo "  curl -X POST http://localhost:8080/a2a/ \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -H 'x-api-key: test-api-key' \\"
echo "    -d @docs/test-queries/simple-test.json"
echo ""
echo "Check status:"
echo "  kubectl --context=$KUBE_CONTEXT get pods -n $NAMESPACE"
echo "  kubectl --context=$KUBE_CONTEXT logs -n $NAMESPACE -l app.kubernetes.io/component=api --tail=50"
echo ""
echo "Troubleshooting:"
echo "  # If A2A doesn't respond, check:"
echo "  # 1. LLM API key: kubectl --context=$KUBE_CONTEXT get secret llm-api-keys -n $NAMESPACE -o yaml"
echo "  # 2. Port-forward: lsof -i :8080"
echo "  # 3. A2A logs: kubectl --context=$KUBE_CONTEXT logs -n $NAMESPACE -l app.kubernetes.io/component=api | grep -i a2a"