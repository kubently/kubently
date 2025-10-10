#!/bin/bash
# Test script for Kubently A2A integration

set -e

echo "Testing Kubently A2A Integration"
echo "================================"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
API_HOST="${API_HOST:-localhost}"
API_PORT="${API_PORT:-8080}"
API_URL="http://${API_HOST}:${API_PORT}"
A2A_URL="http://${API_HOST}:${API_PORT}/a2a"

# Function to check if service is running
check_service() {
    local service_name=$1
    local url=$2
    
    echo -n "Checking ${service_name}... "
    if curl -s -f "${url}/health" > /dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} Running"
        return 0
    else
        echo -e "${RED}✗${NC} Not running"
        return 1
    fi
}

# Function to test A2A endpoint
test_endpoint() {
    local endpoint=$1
    local method=${2:-GET}
    local data=${3:-}
    
    echo -n "Testing ${method} ${endpoint}... "
    
    if [ -z "$data" ]; then
        response=$(curl -s -w "\n%{http_code}" -X ${method} "${A2A_URL}${endpoint}" 2>/dev/null)
    else
        response=$(curl -s -w "\n%{http_code}" -X ${method} \
            -H "Content-Type: application/json" \
            -d "${data}" \
            "${A2A_URL}${endpoint}" 2>/dev/null)
    fi
    
    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | head -n-1)
    
    if [ "$http_code" = "200" ] || [ "$http_code" = "201" ]; then
        echo -e "${GREEN}✓${NC} ${http_code}"
        if [ ! -z "$body" ]; then
            echo "  Response: $(echo $body | jq -c '.' 2>/dev/null || echo $body)"
        fi
        return 0
    else
        echo -e "${RED}✗${NC} ${http_code}"
        if [ ! -z "$body" ]; then
            echo "  Error: $body"
        fi
        return 1
    fi
}

# Main tests
echo ""
echo "1. Checking services..."
echo "-----------------------"
check_service "Main API" "${API_URL}"
check_service "A2A Server" "${A2A_URL}"

echo ""
echo "2. Testing A2A endpoints..."
echo "---------------------------"

# Test agent card
test_endpoint "/"

# Test health endpoint
test_endpoint "/health"

echo ""
echo "3. Testing with A2A client..."
echo "-----------------------------"

# Test with curl using proper authentication
echo "Testing with curl and authentication..."

cat > /tmp/a2a-test.json << EOF
{
  "jsonrpc": "2.0",
  "method": "message/stream",
  "params": {
    "messages": [{
      "messageId": "msg-001",
      "role": "user",
      "parts": [{
        "partId": "part-001",
        "text": "List all available clusters"
      }]
    }]
  },
  "id": "test-001"
}
EOF

echo "Sending test query: 'List all available clusters'"

curl -X POST "${A2A_URL}" \
    -H "Content-Type: application/json" \
    -H "x-api-key: test-api-key" \
    -d @/tmp/a2a-test.json 2>/dev/null || echo -e "${RED}✗${NC} Request failed"

rm -f /tmp/a2a-test.json

echo ""
echo "4. Testing tool discovery..."
echo "----------------------------"

# Test tool listing (if endpoint exists)
if curl -s "${A2A_URL}/tools" > /dev/null 2>&1; then
    test_endpoint "/tools"
else
    echo -e "${YELLOW}⚠${NC} Tools endpoint not available (expected for A2A protocol)"
fi

echo ""
echo "================================"
echo "A2A Integration Test Complete!"
echo ""
echo "To interact with the A2A server manually:"
echo "  curl -X POST http://${A2A_HOST}:${API_PORT}/a2a/ \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -H 'x-api-key: test-api-key' \\"
echo "    -d @docs/test-queries/simple-test.json"
echo ""
echo "Example queries to try:"
echo "  - 'List all pods in default namespace'"
echo "  - 'Show failing deployments'"
echo "  - 'Get logs for nginx pod'"
echo "  - 'Describe service api-gateway'"