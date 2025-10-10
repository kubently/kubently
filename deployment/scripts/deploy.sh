#!/bin/bash
# Deploy script for Kubently to Kubernetes

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
ENVIRONMENT=${1:-staging}
NAMESPACE=${2:-kubently}
CREATE_NAMESPACE=${3:-true}

echo -e "${GREEN}Deploying Kubently to ${ENVIRONMENT} environment...${NC}"

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
MANIFESTS_DIR="$SCRIPT_DIR/../kubernetes"

# Check kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo -e "${RED}kubectl could not be found${NC}"
    exit 1
fi

# Create namespace if needed
if [ "$CREATE_NAMESPACE" == "true" ]; then
    echo -e "${YELLOW}Creating namespace ${NAMESPACE}...${NC}"
    kubectl apply -f ${MANIFESTS_DIR}/namespace.yaml
    
    # Update namespace in manifest if different
    if [ "$NAMESPACE" != "kubently" ]; then
        kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
    fi
fi

# Deploy Redis
echo -e "${YELLOW}Deploying Redis...${NC}"
kubectl apply -f ${MANIFESTS_DIR}/redis/ -n ${NAMESPACE}

# Wait for Redis to be ready
echo -e "${YELLOW}Waiting for Redis to be ready...${NC}"
kubectl wait --for=condition=ready pod -l app=redis -n ${NAMESPACE} --timeout=60s

# Create secrets if they don't exist
echo -e "${YELLOW}Creating secrets...${NC}"

# Check if API keys secret exists
if ! kubectl get secret kubently-api-keys -n ${NAMESPACE} &> /dev/null; then
    echo "Creating API keys secret..."
    kubectl create secret generic kubently-api-keys \
        --namespace ${NAMESPACE} \
        --from-literal=keys="$(openssl rand -hex 16),$(openssl rand -hex 16)"
fi

# Check if agent token secret exists
if ! kubectl get secret kubently-agent-token -n ${NAMESPACE} &> /dev/null; then
    echo "Creating agent token secret..."
    kubectl create secret generic kubently-agent-token \
        --namespace ${NAMESPACE} \
        --from-literal=token="$(openssl rand -hex 32)"
fi

# Deploy API
echo -e "${YELLOW}Deploying API...${NC}"
kubectl apply -f ${MANIFESTS_DIR}/api/configmap.yaml -n ${NAMESPACE}
kubectl apply -f ${MANIFESTS_DIR}/api/deployment.yaml -n ${NAMESPACE}
kubectl apply -f ${MANIFESTS_DIR}/api/service.yaml -n ${NAMESPACE}

# Deploy Ingress if in production
if [ "$ENVIRONMENT" == "production" ]; then
    echo -e "${YELLOW}Deploying Ingress...${NC}"
    kubectl apply -f ${MANIFESTS_DIR}/api/ingress.yaml -n ${NAMESPACE}
fi

# Wait for API to be ready
echo -e "${YELLOW}Waiting for API to be ready...${NC}"
kubectl wait --for=condition=ready pod -l app=kubently-api -n ${NAMESPACE} --timeout=60s

# Deploy Agent
echo -e "${YELLOW}Deploying Agent...${NC}"
kubectl apply -f ${MANIFESTS_DIR}/agent/serviceaccount.yaml -n ${NAMESPACE}
kubectl apply -f ${MANIFESTS_DIR}/agent/rbac.yaml
kubectl apply -f ${MANIFESTS_DIR}/agent/deployment.yaml -n ${NAMESPACE}

# Wait for Agent to be ready
echo -e "${YELLOW}Waiting for Agent to be ready...${NC}"
kubectl wait --for=condition=ready pod -l app=kubently-agent -n ${NAMESPACE} --timeout=60s

echo -e "${GREEN}Deployment complete!${NC}"
echo ""
echo "Deployment summary:"
echo "  Namespace: ${NAMESPACE}"
echo "  Environment: ${ENVIRONMENT}"
echo ""
echo "Components deployed:"
kubectl get deployments -n ${NAMESPACE}
echo ""
echo "Services:"
kubectl get services -n ${NAMESPACE}
echo ""

# Show ingress if deployed
if [ "$ENVIRONMENT" == "production" ]; then
    echo "Ingress:"
    kubectl get ingress -n ${NAMESPACE}
fi