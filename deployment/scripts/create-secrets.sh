#!/bin/bash
# Create secrets for Kubently deployment

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
NAMESPACE=${1:-kubently}
ENVIRONMENT=${2:-staging}

echo -e "${GREEN}Creating secrets for Kubently in ${NAMESPACE}...${NC}"

# Function to generate random string
generate_random() {
    openssl rand -hex ${1:-16}
}

# Check kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo -e "${RED}kubectl could not be found${NC}"
    exit 1
fi

# Check if namespace exists
if ! kubectl get namespace ${NAMESPACE} &> /dev/null; then
    echo -e "${YELLOW}Namespace ${NAMESPACE} doesn't exist. Creating...${NC}"
    kubectl create namespace ${NAMESPACE}
fi

# Create or update API keys secret
echo -e "${YELLOW}Creating API keys secret...${NC}"
if [ "$ENVIRONMENT" == "production" ]; then
    # In production, prompt for API keys
    echo "Enter API keys (comma-separated):"
    read -s API_KEYS
    if [ -z "$API_KEYS" ]; then
        echo -e "${RED}API keys cannot be empty in production${NC}"
        exit 1
    fi
else
    # In staging/dev, generate random keys
    KEY1=$(generate_random 16)
    KEY2=$(generate_random 16)
    KEY3=$(generate_random 16)
    API_KEYS="${KEY1},${KEY2},${KEY3}"
    echo "Generated API keys:"
    echo "  - ${KEY1}"
    echo "  - ${KEY2}"
    echo "  - ${KEY3}"
fi

kubectl create secret generic kubently-api-keys \
    --namespace ${NAMESPACE} \
    --from-literal=keys="${API_KEYS}" \
    --dry-run=client -o yaml | kubectl apply -f -

check_status "API keys secret created/updated"

# Create or update agent token secret
echo -e "${YELLOW}Creating agent token secret...${NC}"
AGENT_TOKEN=$(generate_random 32)

kubectl create secret generic kubently-agent-token \
    --namespace ${NAMESPACE} \
    --from-literal=token="${AGENT_TOKEN}" \
    --dry-run=client -o yaml | kubectl apply -f -

echo "Generated agent token: ${AGENT_TOKEN}"
check_status "Agent token secret created/updated"

# Create TLS secret for production
if [ "$ENVIRONMENT" == "production" ]; then
    echo -e "${YELLOW}Creating TLS certificate secret...${NC}"
    
    # Check if cert files exist
    if [ -f "tls.crt" ] && [ -f "tls.key" ]; then
        kubectl create secret tls kubently-api-tls \
            --namespace ${NAMESPACE} \
            --cert=tls.crt \
            --key=tls.key \
            --dry-run=client -o yaml | kubectl apply -f -
        check_status "TLS secret created/updated"
    else
        echo -e "${YELLOW}TLS certificate files not found. Skipping TLS secret creation.${NC}"
        echo "To create TLS secret, place tls.crt and tls.key in current directory"
    fi
fi

# Verify secrets were created
echo ""
echo -e "${GREEN}Verifying secrets...${NC}"
kubectl get secrets -n ${NAMESPACE} | grep kubently

echo ""
echo -e "${GREEN}Secrets created successfully!${NC}"
echo ""
echo "Summary:"
echo "  Namespace: ${NAMESPACE}"
echo "  Environment: ${ENVIRONMENT}"
echo ""
echo "Secrets created:"
echo "  - kubently-api-keys"
echo "  - kubently-agent-token"
if [ "$ENVIRONMENT" == "production" ] && [ -f "tls.crt" ]; then
    echo "  - kubently-api-tls"
fi

# Function to check command success
check_status() {
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ $1${NC}"
    else
        echo -e "${RED}✗ $1${NC}"
        exit 1
    fi
}