#!/bin/bash
# Docker Compose initialization script
# Registers the executor token with the API for local development

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Configuration
API_URL="http://localhost:8080"
API_KEY="test-api-key"
CLUSTER_ID="local-dev"
EXECUTOR_TOKEN="local-dev-token"

echo -e "${YELLOW}Initializing Docker Compose environment...${NC}"

# Wait for API to be healthy
echo -n "Waiting for API to be ready..."
MAX_RETRIES=30
RETRY_COUNT=0
while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if curl -s -f "${API_URL}/healthz" > /dev/null 2>&1; then
        echo -e " ${GREEN}✓${NC}"
        break
    fi
    echo -n "."
    sleep 2
    RETRY_COUNT=$((RETRY_COUNT + 1))
done

if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
    echo -e " ${RED}✗${NC}"
    echo -e "${RED}Error: API did not become healthy${NC}"
    exit 1
fi

# Register executor token
echo -n "Registering executor token for cluster '${CLUSTER_ID}'..."
RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X POST \
    -H "X-API-Key: ${API_KEY}" \
    -H "Content-Type: application/json" \
    "${API_URL}/admin/agents/${CLUSTER_ID}/token" 2>&1)

HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | head -n-1)

if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "201" ]; then
    echo -e " ${GREEN}✓${NC}"
    echo -e "${GREEN}Executor token registered successfully${NC}"
    echo ""
    echo "Configuration:"
    echo "  API URL: ${API_URL}"
    echo "  Cluster ID: ${CLUSTER_ID}"
    echo "  Executor Token: ${EXECUTOR_TOKEN}"
    echo ""
    echo -e "${GREEN}✨ Docker Compose environment is ready!${NC}"
    echo ""
    echo "Test the API:"
    echo "  curl ${API_URL}/healthz"
    echo ""
    echo "View logs:"
    echo "  docker compose -f deployment/docker-compose.yaml logs -f"
    echo ""
    echo "Test A2A endpoint:"
    echo "  curl -X POST ${API_URL}/a2a/ \\"
    echo "    -H 'Content-Type: application/json' \\"
    echo "    -d '{\"jsonrpc\": \"2.0\", \"method\": \"agent/card\", \"id\": 1}'"
    exit 0
elif [ "$HTTP_CODE" = "409" ]; then
    echo -e " ${YELLOW}(already exists)${NC}"
    echo -e "${YELLOW}Token already registered - this is fine for local dev${NC}"
    echo -e "${GREEN}✨ Docker Compose environment is ready!${NC}"
    exit 0
else
    echo -e " ${RED}✗${NC}"
    echo -e "${RED}Error: Failed to register token (HTTP ${HTTP_CODE})${NC}"
    echo -e "${RED}Response: ${BODY}${NC}"
    echo ""
    echo "Troubleshooting:"
    echo "  1. Check API logs: docker compose -f deployment/docker-compose.yaml logs api"
    echo "  2. Verify API_KEY matches: ${API_KEY}"
    echo "  3. Check API is running: curl ${API_URL}/healthz"
    exit 1
fi
