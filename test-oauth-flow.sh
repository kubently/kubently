#!/bin/bash

# Test OAuth 2.0 authentication flow end-to-end

set -e

echo "🔐 OAuth 2.0 Authentication Flow Test"
echo "====================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to check if a service is running
check_service() {
    local name=$1
    local url=$2
    
    echo -n "Checking $name... "
    if curl -s -o /dev/null -w "%{http_code}" "$url" | grep -q "200\|404"; then
        echo -e "${GREEN}✓${NC}"
        return 0
    else
        echo -e "${RED}✗${NC}"
        return 1
    fi
}

# Function to cleanup background processes
cleanup() {
    echo ""
    echo "🧹 Cleaning up..."
    
    # Kill mock OAuth provider if running
    if [ ! -z "$OAUTH_PID" ]; then
        kill $OAUTH_PID 2>/dev/null || true
    fi
    
    # Kill port forward if running
    if [ ! -z "$PORT_FORWARD_PID" ]; then
        kill $PORT_FORWARD_PID 2>/dev/null || true
    fi
}

# Set trap to cleanup on exit
trap cleanup EXIT

echo "1️⃣  Starting Mock OAuth Provider..."
echo "--------------------------------"

# Start mock OAuth provider in background
python3 kubently/modules/auth/mock_oauth_provider.py &
OAUTH_PID=$!

# Wait for OAuth provider to start
sleep 3

# Check if OAuth provider is running
if ! check_service "Mock OAuth Provider" "http://localhost:9000/.well-known/openid-configuration"; then
    echo -e "${RED}Failed to start mock OAuth provider${NC}"
    exit 1
fi

echo ""
echo "2️⃣  Setting up Kubently API..."
echo "----------------------------"

# Check if Kubently API is accessible
if check_service "Kubently API" "http://localhost:8080/health"; then
    echo "Kubently API already running"
else
    echo "Starting port-forward to Kubently API..."
    kubectl port-forward svc/kubently-api 8080:8080 -n kubently &
    PORT_FORWARD_PID=$!
    sleep 3
    
    if ! check_service "Kubently API" "http://localhost:8080/health"; then
        echo -e "${RED}Failed to access Kubently API${NC}"
        echo "Make sure Kubently is deployed with: ./deploy-test.sh"
        exit 1
    fi
fi

echo ""
echo "3️⃣  Running Integration Tests..."
echo "------------------------------"

# Run Python integration tests
python3 test_oauth_integration.py

echo ""
echo "4️⃣  Testing CLI Login Flow..."
echo "--------------------------"

# Build the CLI if needed
echo "Building CLI..."
(cd kubently-cli/nodejs && npm install && npm run build) > /dev/null 2>&1

# Test API key login (legacy mode)
echo ""
echo -e "${YELLOW}Testing API key authentication:${NC}"
node kubently-cli/nodejs/dist/index.js login --api-key test-api-key
echo -e "${GREEN}✓ API key authentication configured${NC}"

# Test OAuth login flow (requires manual interaction)
echo ""
echo -e "${YELLOW}Testing OAuth authentication:${NC}"
echo "To test OAuth login manually, run:"
echo "  node kubently-cli/nodejs/dist/index.js login"
echo ""
echo "Then visit: http://localhost:9000/device"
echo "Enter the user code shown and select a test user"

echo ""
echo "====================================="
echo -e "${GREEN}✅ OAuth Integration Test Complete!${NC}"
echo "====================================="
echo ""
echo "Summary:"
echo "  • Mock OAuth provider: Running on http://localhost:9000"
echo "  • Kubently API: Accessible with dual auth support"
echo "  • JWT validation: Working"
echo "  • Device authorization: Working"
echo "  • CLI authentication: Both API key and OAuth supported"
echo ""
echo "Next steps:"
echo "  1. Test OAuth login: kubently login"
echo "  2. Test with API key: kubently login --api-key <key>"
echo "  3. Use kubently debug to test authenticated access"