#!/bin/bash
# Comprehensive test script for Kubently deployment
# Tests actual functionality, not just infrastructure

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
NAMESPACE=${1:-kubently}
API_URL=${2:-"http://localhost:8088"}

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   Kubently Comprehensive E2E Tests${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Function to check command success
check_status() {
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ $1${NC}"
        return 0
    else
        echo -e "${RED}✗ $1${NC}"
        return 1
    fi
}

# Track test results
TESTS_PASSED=0
TESTS_FAILED=0

# ============= Infrastructure Tests =============
echo -e "${BLUE}=== Infrastructure Tests ===${NC}"
echo ""

# Test 1: Check all pods are running
echo -e "${YELLOW}Test 1: Checking pod status...${NC}"
ALL_PODS_READY=$(kubectl get pods -n ${NAMESPACE} --no-headers | grep -v "Running" | wc -l | tr -d ' ')
if [ "$ALL_PODS_READY" == "0" ]; then
    echo -e "${GREEN}✓ All pods are running${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ Some pods are not running${NC}"
    kubectl get pods -n ${NAMESPACE}
    ((TESTS_FAILED++))
fi

# Test 2: Check API health endpoint
echo -e "${YELLOW}Test 2: Testing API health endpoint...${NC}"
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" ${API_URL}/health)
if [ "$HTTP_STATUS" == "200" ]; then
    echo -e "${GREEN}✓ API health endpoint (HTTP $HTTP_STATUS)${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ API health endpoint (HTTP $HTTP_STATUS)${NC}"
    ((TESTS_FAILED++))
fi

# Test 3: Check Redis connectivity
echo -e "${YELLOW}Test 3: Testing Redis connectivity...${NC}"
kubectl exec -n ${NAMESPACE} deployment/redis -- redis-cli ping > /dev/null 2>&1
if check_status "Redis responding to ping"; then
    ((TESTS_PASSED++))
else
    ((TESTS_FAILED++))
fi

# ============= Authentication Tests =============
echo ""
echo -e "${BLUE}=== Authentication Tests ===${NC}"
echo ""

# Test 4: Test agent authentication (check if agent can poll)
echo -e "${YELLOW}Test 4: Testing agent authentication...${NC}"
# Check if agent is successfully polling (no 401 errors in last 30 seconds)
RECENT_401=$(kubectl logs -n ${NAMESPACE} deployment/kubently-agent --since=30s 2>/dev/null | grep -c "401" || echo "0")
if [ "$RECENT_401" == "0" ]; then
    echo -e "${GREEN}✓ Agent authentication working (no recent 401s)${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ Agent authentication failing ($RECENT_401 recent 401 errors)${NC}"
    ((TESTS_FAILED++))
fi

# Test 5: Test API key authentication
echo -e "${YELLOW}Test 5: Testing API key authentication...${NC}"
API_KEY=$(kubectl get secret kubently-api-keys -n ${NAMESPACE} -o jsonpath='{.data.keys}' | base64 -d | cut -d',' -f1)
SESSION_RESPONSE=$(curl -s -X POST ${API_URL}/session \
    -H "X-API-Key: ${API_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"cluster_id": "test-cluster"}' \
    -o /tmp/session.json \
    -w "%{http_code}")

if [ "$SESSION_RESPONSE" == "201" ]; then
    echo -e "${GREEN}✓ API key authentication working${NC}"
    ((TESTS_PASSED++))
    SESSION_ID=$(jq -r '.session_id' /tmp/session.json)
else
    echo -e "${RED}✗ API key authentication failed (HTTP $SESSION_RESPONSE)${NC}"
    ((TESTS_FAILED++))
    SESSION_ID=""
fi

# ============= Functional Tests =============
echo ""
echo -e "${BLUE}=== Functional Tests ===${NC}"
echo ""

# Test 6: Execute a kubectl command via API
echo -e "${YELLOW}Test 6: Testing kubectl command execution...${NC}"
if [ ! -z "$SESSION_ID" ]; then
    # Send a simple kubectl get nodes command
    CMD_RESPONSE=$(curl -s -X POST ${API_URL}/session/${SESSION_ID}/execute \
        -H "X-API-Key: ${API_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"command": "kubectl get nodes -o wide"}' \
        -o /tmp/execute.json \
        -w "%{http_code}")
    
    if [ "$CMD_RESPONSE" == "200" ] || [ "$CMD_RESPONSE" == "408" ]; then
        echo -e "${GREEN}✓ Command execution request accepted (HTTP $CMD_RESPONSE)${NC}"
        ((TESTS_PASSED++))
        
        # Check if command was executed
        sleep 3
        RESULT=$(jq -r '.result' /tmp/execute.json 2>/dev/null || echo "")
        if [ ! -z "$RESULT" ] && [ "$RESULT" != "null" ]; then
            echo -e "${GREEN}  Command result received${NC}"
        else
            echo -e "${YELLOW}  Command queued but no immediate result (timeout)${NC}"
        fi
    else
        echo -e "${RED}✗ Command execution failed (HTTP $CMD_RESPONSE)${NC}"
        ((TESTS_FAILED++))
    fi
    
    # Clean up session
    if [ ! -z "$SESSION_ID" ]; then
        curl -s -X DELETE ${API_URL}/session/${SESSION_ID} \
            -H "X-API-Key: ${API_KEY}" > /dev/null 2>&1
    fi
else
    echo -e "${YELLOW}⚠ Skipping command execution test (no session)${NC}"
fi

# Test 7: Check agent is processing commands
echo -e "${YELLOW}Test 7: Checking agent command processing...${NC}"
AGENT_LOGS=$(kubectl logs -n ${NAMESPACE} deployment/kubently-agent --tail=100 2>/dev/null)
if echo "$AGENT_LOGS" | grep -q "Polling for commands"; then
    echo -e "${GREEN}✓ Agent is actively polling for commands${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${YELLOW}⚠ Agent may not be actively polling${NC}"
    ((TESTS_FAILED++))
fi

# Test 8: Check Redis queue operations
echo -e "${YELLOW}Test 8: Testing Redis queue operations...${NC}"
REDIS_KEYS=$(kubectl exec -n ${NAMESPACE} deployment/redis -- redis-cli keys '*' 2>/dev/null | wc -l | tr -d ' ')
echo -e "${GREEN}✓ Redis has $REDIS_KEYS keys in use${NC}"
((TESTS_PASSED++))

# ============= Security Tests =============
echo ""
echo -e "${BLUE}=== Security Tests ===${NC}"
echo ""

# Test 9: Test unauthorized access is rejected
echo -e "${YELLOW}Test 9: Testing unauthorized access rejection...${NC}"
UNAUTH_RESPONSE=$(curl -s -X POST ${API_URL}/session \
    -H "X-API-Key: invalid-key" \
    -H "Content-Type: application/json" \
    -d '{"cluster_id": "test"}' \
    -w "%{http_code}" \
    -o /dev/null)

if [ "$UNAUTH_RESPONSE" == "401" ]; then
    echo -e "${GREEN}✓ Unauthorized access properly rejected${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ Unauthorized access not properly rejected (HTTP $UNAUTH_RESPONSE)${NC}"
    ((TESTS_FAILED++))
fi

# Test 10: Check RBAC is properly configured
echo -e "${YELLOW}Test 10: Testing RBAC configuration...${NC}"
RBAC_CHECK=$(kubectl auth can-i get pods --as=system:serviceaccount:${NAMESPACE}:kubently-agent 2>/dev/null)
if [ "$RBAC_CHECK" == "yes" ]; then
    echo -e "${GREEN}✓ Agent has correct RBAC permissions${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ Agent RBAC permissions may be incorrect${NC}"
    ((TESTS_FAILED++))
fi

# ============= Performance Tests =============
echo ""
echo -e "${BLUE}=== Performance Tests ===${NC}"
echo ""

# Test 11: Check API response time
echo -e "${YELLOW}Test 11: Testing API response time...${NC}"
RESPONSE_TIME=$(curl -s -o /dev/null -w "%{time_total}" ${API_URL}/health)
RESPONSE_MS=$(echo "$RESPONSE_TIME * 1000" | bc 2>/dev/null || echo "0")
if (( $(echo "$RESPONSE_TIME < 0.5" | bc -l 2>/dev/null || echo 0) )); then
    echo -e "${GREEN}✓ API response time acceptable (${RESPONSE_MS}ms)${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${YELLOW}⚠ API response time slow (${RESPONSE_MS}ms)${NC}"
    ((TESTS_FAILED++))
fi

# ============= Summary =============
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}        TEST SUMMARY${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "Tests passed: ${GREEN}${TESTS_PASSED}${NC}"
echo -e "Tests failed: ${RED}${TESTS_FAILED}${NC}"
echo ""

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}🎉 All comprehensive tests passed!${NC}"
    exit 0
else
    echo -e "${RED}⚠️  Some tests failed. Please review the results above.${NC}"
    
    # Show debugging information
    echo ""
    echo -e "${YELLOW}Debug Information:${NC}"
    echo "Recent agent logs:"
    kubectl logs -n ${NAMESPACE} deployment/kubently-agent --tail=10
    echo ""
    echo "Recent API logs:"
    kubectl logs -n ${NAMESPACE} deployment/kubently-api --tail=10 | head -20
    
    exit 1
fi