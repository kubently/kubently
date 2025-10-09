#!/bin/bash
# Test script for Kubently deployment

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
NAMESPACE=${1:-kubently}
API_URL=${2:-"http://localhost:8080"}

echo -e "${GREEN}Testing Kubently deployment...${NC}"

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

# Test 1: Check Redis connection
echo -e "${YELLOW}Testing Redis connection...${NC}"
if command -v redis-cli &> /dev/null; then
    redis-cli -h localhost -p 6379 ping > /dev/null 2>&1
    if check_status "Redis connection"; then
        ((TESTS_PASSED++))
    else
        ((TESTS_FAILED++))
    fi
else
    echo "redis-cli not found, testing via kubectl..."
    kubectl exec -n ${NAMESPACE} deployment/redis -- redis-cli ping > /dev/null 2>&1
    if check_status "Redis connection (via kubectl)"; then
        ((TESTS_PASSED++))
    else
        ((TESTS_FAILED++))
    fi
fi

# Test 2: Check API health endpoint
echo -e "${YELLOW}Testing API health endpoint...${NC}"
if command -v curl &> /dev/null; then
    HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" ${API_URL}/health)
    if [ "$HTTP_STATUS" == "200" ]; then
        echo -e "${GREEN}✓ API health endpoint (HTTP $HTTP_STATUS)${NC}"
        ((TESTS_PASSED++))
    else
        echo -e "${RED}✗ API health endpoint (HTTP $HTTP_STATUS)${NC}"
        ((TESTS_FAILED++))
    fi
else
    echo "curl not found, skipping API test"
fi

# Test 3: Check deployments are running
echo -e "${YELLOW}Testing Kubernetes deployments...${NC}"

# Check Redis deployment
REDIS_READY=$(kubectl get deployment redis -n ${NAMESPACE} -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
REDIS_DESIRED=$(kubectl get deployment redis -n ${NAMESPACE} -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "1")
if [ "$REDIS_READY" == "$REDIS_DESIRED" ] && [ "$REDIS_READY" != "0" ]; then
    echo -e "${GREEN}✓ Redis deployment ($REDIS_READY/$REDIS_DESIRED replicas ready)${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ Redis deployment ($REDIS_READY/$REDIS_DESIRED replicas ready)${NC}"
    ((TESTS_FAILED++))
fi

# Check API deployment
API_READY=$(kubectl get deployment kubently-api -n ${NAMESPACE} -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
API_DESIRED=$(kubectl get deployment kubently-api -n ${NAMESPACE} -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "2")
if [ "$API_READY" == "$API_DESIRED" ] && [ "$API_READY" != "0" ]; then
    echo -e "${GREEN}✓ API deployment ($API_READY/$API_DESIRED replicas ready)${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ API deployment ($API_READY/$API_DESIRED replicas ready)${NC}"
    ((TESTS_FAILED++))
fi

# Check Agent deployment
AGENT_READY=$(kubectl get deployment kubently-agent -n ${NAMESPACE} -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
AGENT_DESIRED=$(kubectl get deployment kubently-agent -n ${NAMESPACE} -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "1")
if [ "$AGENT_READY" == "$AGENT_DESIRED" ] && [ "$AGENT_READY" != "0" ]; then
    echo -e "${GREEN}✓ Agent deployment ($AGENT_READY/$AGENT_DESIRED replicas ready)${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ Agent deployment ($AGENT_READY/$AGENT_DESIRED replicas ready)${NC}"
    ((TESTS_FAILED++))
fi

# Test 4: Check services
echo -e "${YELLOW}Testing Kubernetes services...${NC}"

# Check Redis service
kubectl get service redis -n ${NAMESPACE} > /dev/null 2>&1
if check_status "Redis service exists"; then
    ((TESTS_PASSED++))
else
    ((TESTS_FAILED++))
fi

# Check API service
kubectl get service kubently-api -n ${NAMESPACE} > /dev/null 2>&1
if check_status "API service exists"; then
    ((TESTS_PASSED++))
else
    ((TESTS_FAILED++))
fi

# Test 5: Check agent logs for errors
echo -e "${YELLOW}Checking agent logs...${NC}"
AGENT_ERRORS=$(kubectl logs -n ${NAMESPACE} deployment/kubently-agent --tail=50 2>/dev/null | grep -i error | wc -l | tr -d ' ' || echo "0")
if [ "$AGENT_ERRORS" == "0" ]; then
    echo -e "${GREEN}✓ No errors in agent logs${NC}"
    ((TESTS_PASSED++))
else
    echo -e "${YELLOW}⚠ Found $AGENT_ERRORS error(s) in agent logs${NC}"
    ((TESTS_FAILED++))
fi

# Test 6: Check RBAC
echo -e "${YELLOW}Testing RBAC configuration...${NC}"
kubectl get clusterrole kubently-agent-readonly > /dev/null 2>&1
if check_status "Agent ClusterRole exists"; then
    ((TESTS_PASSED++))
else
    ((TESTS_FAILED++))
fi

# Summary
echo ""
echo -e "${GREEN}========== TEST SUMMARY ==========${NC}"
echo -e "Tests passed: ${GREEN}${TESTS_PASSED}${NC}"
echo -e "Tests failed: ${RED}${TESTS_FAILED}${NC}"

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed!${NC}"
    exit 1
fi