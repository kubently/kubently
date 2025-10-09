#!/bin/bash
# Deploy Kubently using Helm

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
RELEASE_NAME=${1:-kubently}
NAMESPACE=${2:-kubently}
VALUES_FILE=${3:-}
ENVIRONMENT=${4:-staging}

echo -e "${GREEN}Deploying Kubently with Helm...${NC}"

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CHART_DIR="$SCRIPT_DIR/../helm/kubently"

# Check helm is available
if ! command -v helm &> /dev/null; then
    echo -e "${RED}helm could not be found${NC}"
    exit 1
fi

# Update dependencies
echo -e "${YELLOW}Updating Helm dependencies...${NC}"
helm dependency update ${CHART_DIR}

# Prepare values based on environment
if [ -z "$VALUES_FILE" ]; then
    case "$ENVIRONMENT" in
        production)
            VALUES_FILE="${CHART_DIR}/values.production.yaml"
            ;;
        staging)
            VALUES_FILE="${CHART_DIR}/values.staging.yaml"
            ;;
        *)
            VALUES_FILE="${CHART_DIR}/values.yaml"
            ;;
    esac
fi

# Check if values file exists
if [ ! -f "$VALUES_FILE" ] && [ "$VALUES_FILE" != "${CHART_DIR}/values.yaml" ]; then
    echo -e "${YELLOW}Values file ${VALUES_FILE} not found, using default values${NC}"
    VALUES_FILE="${CHART_DIR}/values.yaml"
fi

# Install or upgrade release
echo -e "${YELLOW}Installing/upgrading Helm release...${NC}"
helm upgrade --install ${RELEASE_NAME} ${CHART_DIR} \
    --namespace ${NAMESPACE} \
    --create-namespace \
    --values ${VALUES_FILE} \
    --wait \
    --timeout 5m

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Helm deployment successful${NC}"
else
    echo -e "${RED}✗ Helm deployment failed${NC}"
    exit 1
fi

# Show deployment status
echo ""
echo -e "${GREEN}Deployment Status:${NC}"
helm status ${RELEASE_NAME} -n ${NAMESPACE}

echo ""
echo -e "${GREEN}Resources deployed:${NC}"
kubectl get all -n ${NAMESPACE} -l "app.kubernetes.io/instance=${RELEASE_NAME}"

echo ""
echo -e "${GREEN}Deployment complete!${NC}"
echo ""
echo "To access the application:"
echo "  1. Port forward the API service:"
echo "     kubectl port-forward -n ${NAMESPACE} svc/${RELEASE_NAME}-api 8080:80"
echo "  2. Access the API at http://localhost:8080"
echo ""
echo "To view logs:"
echo "  API logs: kubectl logs -n ${NAMESPACE} -l app.kubernetes.io/component=api -f"
echo "  Agent logs: kubectl logs -n ${NAMESPACE} -l app.kubernetes.io/component=agent -f"
echo ""
echo "To uninstall:"
echo "  helm uninstall ${RELEASE_NAME} -n ${NAMESPACE}"