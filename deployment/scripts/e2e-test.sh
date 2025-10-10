#!/bin/bash
# End-to-end test script for Kubently with Kind

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
CLUSTER_NAME="kubently-e2e"
NAMESPACE="kubently"
VERSION="e2e-$(date +%Y%m%d%H%M%S)"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   Kubently End-to-End Test Setup${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$SCRIPT_DIR/../.."
DEPLOYMENT_DIR="$SCRIPT_DIR/.."

cd "$PROJECT_ROOT"

# Function to handle errors
handle_error() {
    echo -e "${RED}Error: $1${NC}"
    echo -e "${YELLOW}Cleaning up...${NC}"
    cleanup
    exit 1
}

# Function to cleanup
cleanup() {
    echo -e "${YELLOW}Deleting Kind cluster...${NC}"
    kind delete cluster --name ${CLUSTER_NAME} 2>/dev/null || true
}

# Trap errors
trap 'handle_error "Script failed at line $LINENO"' ERR

# Step 1: Check prerequisites
echo -e "${GREEN}Step 1: Checking prerequisites...${NC}"
command -v docker >/dev/null 2>&1 || handle_error "Docker is not installed"
command -v kind >/dev/null 2>&1 || handle_error "Kind is not installed"
command -v kubectl >/dev/null 2>&1 || handle_error "kubectl is not installed"

echo -e "${GREEN}✓ All prerequisites met${NC}"
echo ""

# Step 2: Build Docker images
echo -e "${GREEN}Step 2: Building Docker images...${NC}"
${SCRIPT_DIR}/build.sh ${VERSION} kubently false

if [ $? -ne 0 ]; then
    handle_error "Failed to build Docker images"
fi
echo -e "${GREEN}✓ Docker images built successfully${NC}"
echo ""

# Step 3: Create Kind cluster
echo -e "${GREEN}Step 3: Creating Kind cluster...${NC}"

# Check if cluster already exists
if kind get clusters | grep -q ${CLUSTER_NAME}; then
    echo -e "${YELLOW}Cluster ${CLUSTER_NAME} already exists, deleting...${NC}"
    kind delete cluster --name ${CLUSTER_NAME}
fi

# Create cluster with configuration
kind create cluster --config=${DEPLOYMENT_DIR}/kind-config.yaml --name=${CLUSTER_NAME}

if [ $? -ne 0 ]; then
    handle_error "Failed to create Kind cluster"
fi

# Set kubectl context
kubectl cluster-info --context kind-${CLUSTER_NAME}
echo -e "${GREEN}✓ Kind cluster created successfully${NC}"
echo ""

# Step 4: Load Docker images into Kind
echo -e "${GREEN}Step 4: Loading Docker images into Kind cluster...${NC}"

kind load docker-image kubently/api:${VERSION} --name ${CLUSTER_NAME}
kind load docker-image kubently/agent:${VERSION} --name ${CLUSTER_NAME}

# Also load with latest tag for deployment
kind load docker-image kubently/api:latest --name ${CLUSTER_NAME}
kind load docker-image kubently/agent:latest --name ${CLUSTER_NAME}

echo -e "${GREEN}✓ Docker images loaded into Kind${NC}"
echo ""

# Step 5: Install NGINX Ingress Controller (optional)
echo -e "${GREEN}Step 5: Installing NGINX Ingress Controller...${NC}"
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml

# Wait for ingress controller to be ready
echo -e "${YELLOW}Waiting for ingress controller to be ready...${NC}"
kubectl wait --namespace ingress-nginx \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller \
  --timeout=90s

echo -e "${GREEN}✓ NGINX Ingress Controller installed${NC}"
echo ""

# Step 6: Deploy Kubently
echo -e "${GREEN}Step 6: Deploying Kubently to Kind cluster...${NC}"

# Use the deploy script
${SCRIPT_DIR}/deploy.sh local ${NAMESPACE} true

if [ $? -ne 0 ]; then
    handle_error "Failed to deploy Kubently"
fi

echo -e "${GREEN}✓ Kubently deployed successfully${NC}"
echo ""

# Step 7: Wait for all components to be ready
echo -e "${GREEN}Step 7: Waiting for all components to be ready...${NC}"

# Wait for deployments with increased timeout
echo "Waiting for Redis..."
kubectl wait --for=condition=available deployment/redis -n ${NAMESPACE} --timeout=120s || true

echo "Waiting for API..."
kubectl wait --for=condition=available deployment/kubently-api -n ${NAMESPACE} --timeout=120s || true

echo "Waiting for Agent..."
kubectl wait --for=condition=available deployment/kubently-agent -n ${NAMESPACE} --timeout=120s || true

# Give services a moment to fully initialize
sleep 10

echo -e "${GREEN}✓ All components are ready${NC}"
echo ""

# Step 8: Port forwarding for API access
echo -e "${GREEN}Step 8: Setting up port forwarding...${NC}"

# Kill any existing port-forward
pkill -f "kubectl port-forward.*8088:80" 2>/dev/null || true

# Start port-forward in background
kubectl port-forward -n ${NAMESPACE} service/kubently-api 8088:80 &
PF_PID=$!
sleep 3

echo -e "${GREEN}✓ Port forwarding established (PID: $PF_PID)${NC}"
echo ""

# Step 9: Run tests
echo -e "${GREEN}Step 9: Running end-to-end tests...${NC}"
echo ""

# Run the test deployment script
${SCRIPT_DIR}/test-deployment.sh ${NAMESPACE} "http://localhost:8088"

TEST_RESULT=$?

# Kill port-forward
kill $PF_PID 2>/dev/null || true

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   Test Results${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Show pod status
echo -e "${YELLOW}Pod Status:${NC}"
kubectl get pods -n ${NAMESPACE}
echo ""

# Show recent events
echo -e "${YELLOW}Recent Events:${NC}"
kubectl get events -n ${NAMESPACE} --sort-by='.lastTimestamp' | tail -10
echo ""

# Check logs for errors
echo -e "${YELLOW}Checking logs for errors...${NC}"
echo "API logs:"
kubectl logs -n ${NAMESPACE} deployment/kubently-api --tail=20
echo ""
echo "Agent logs:"
kubectl logs -n ${NAMESPACE} deployment/kubently-agent --tail=20
echo ""

if [ $TEST_RESULT -eq 0 ]; then
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}   E2E TESTS PASSED SUCCESSFULLY!${NC}"
    echo -e "${GREEN}========================================${NC}"
    
    echo ""
    echo -e "${YELLOW}To access the cluster:${NC}"
    echo "  kubectl config use-context kind-${CLUSTER_NAME}"
    echo "  kubectl port-forward -n ${NAMESPACE} service/kubently-api 8088:80"
    echo ""
    echo -e "${YELLOW}To cleanup:${NC}"
    echo "  kind delete cluster --name ${CLUSTER_NAME}"
    echo ""
else
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}   E2E TESTS FAILED${NC}"
    echo -e "${RED}========================================${NC}"
    echo ""
    echo -e "${YELLOW}Debug commands:${NC}"
    echo "  kubectl get all -n ${NAMESPACE}"
    echo "  kubectl describe pod -n ${NAMESPACE}"
    echo "  kubectl logs -n ${NAMESPACE} deployment/kubently-api"
    echo "  kubectl logs -n ${NAMESPACE} deployment/kubently-agent"
    echo ""
    
    read -p "Do you want to keep the cluster for debugging? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        cleanup
    fi
    
    exit 1
fi