#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

KUBE_CONTEXT="kind-kubently"

# Function to check if Python A2A client is available
check_a2a_client() {
  if command -v python3 &> /dev/null && python3 -c "import httpx" 2>/dev/null; then
    return 0
  else
    return 1
  fi
}

echo -e "${YELLOW}🧪 Testing Kubently A2A endpoint...${NC}"
echo -e "   Context: $KUBE_CONTEXT"

# Check if API is reachable
echo -n "Checking API health... "
if curl -s http://localhost:8080/health | grep -q "healthy"; then
  echo -e "${GREEN}✅ API is healthy${NC}"
else
  echo -e "${RED}❌ API is not reachable${NC}"
  echo "Please ensure port-forward is active:"
  echo "  kubectl --context=$KUBE_CONTEXT port-forward -n kubently svc/kubently-api 8080:8080 &"
  exit 1
fi

# Test 1: Query without cluster
echo -e "\n${YELLOW}Test 1: Query without cluster specified${NC}"

if check_a2a_client; then
  # Use Python A2A test client
  echo "Using Python A2A test client..."
  response=$(python3 test-automation/a2a_test_client.py \
    "http://localhost:8080" \
    "test-api-key" \
    "what pods are running in the kubently namespace?" 2>/dev/null)
  
  if [ $? -eq 0 ]; then
    final_text=$(echo "$response" | python3 -c "import sys, json; print(json.load(sys.stdin).get('final_text', ''))")
    
    if [[ $final_text == *"Which cluster"* ]] || [[ $final_text == *"specify which cluster"* ]] || [[ $final_text == *"multiple clusters"* ]]; then
      echo -e "${GREEN}✅ Test 1 passed: Agent correctly asks for cluster specification${NC}"
    else
      echo -e "${RED}❌ Test 1 failed: Unexpected response${NC}"
      echo "Response: $final_text"
      exit 1
    fi
  else
    echo -e "${RED}❌ Test 1 failed: A2A client error${NC}"
    exit 1
  fi
else
  # Fallback to curl
  echo "A2A Python client not available, using curl fallback..."
  response=$(curl -sL -X POST http://localhost:8080/a2a/ \
    -H "Content-Type: application/json" \
    -H "X-Api-Key: test-api-key" \
    -d '{
      "jsonrpc": "2.0",
      "method": "message/stream",
      "params": {
        "message": {
          "messageId": "test-1-'$(date +%s)'",
          "role": "user",
          "parts": [{"partId": "p1", "text": "what pods are running in the kubently namespace?"}]
        }
      },
      "id": 1
    }' 2>/dev/null)

  # Extract text from either status-update or artifact-update
  status_text=$(echo "$response" | grep '"status-update"' | grep -o '"text":"[^"]*"' | cut -d'"' -f4 | tail -1)
  artifact_text=$(echo "$response" | grep '"artifact-update"' | grep -o '"text":"[^"]*"' | cut -d'"' -f4 | tail -1)

  # Check both possible locations for the response text
  if [[ $status_text == *"Which cluster"* ]] || [[ $artifact_text == *"Which cluster"* ]] || 
     [[ $status_text == *"specify which cluster"* ]] || [[ $artifact_text == *"specify which cluster"* ]] ||
     [[ $status_text == *"multiple clusters"* ]] || [[ $artifact_text == *"multiple clusters"* ]]; then
    echo -e "${GREEN}✅ Test 1 passed: Agent correctly asks for cluster specification${NC}"
  else
    echo -e "${RED}❌ Test 1 failed: Unexpected response${NC}"
    echo "Status text: ${status_text:0:200}"
    echo "Artifact text: ${artifact_text:0:200}"
    exit 1
  fi
fi

# Test 2: Query with cluster specified
echo -e "\n${YELLOW}Test 2: Query with cluster specified${NC}"

if check_a2a_client; then
  # Use Python A2A test client
  echo "Using Python A2A test client..."
  response=$(python3 test-automation/a2a_test_client.py \
    "http://localhost:8080" \
    "test-api-key" \
    "list pods in kubently namespace in kind cluster" 2>/dev/null)
  
  if [ $? -eq 0 ]; then
    final_text=$(echo "$response" | python3 -c "import sys, json; print(json.load(sys.stdin).get('final_text', ''))")
    
    if [[ $final_text == *"kubently-api"* ]] || [[ $final_text == *"kubently-executor"* ]] || [[ $final_text == *"Running"* ]]; then
      echo -e "${GREEN}✅ Test 2 passed: Agent returns pod information${NC}"
    else
      echo -e "${RED}❌ Test 2 failed: Unexpected response${NC}"
      echo "Response: $final_text"
      exit 1
    fi
  else
    echo -e "${RED}❌ Test 2 failed: A2A client error${NC}"
    exit 1
  fi
else
  # Fallback to curl
  echo "A2A Python client not available, using curl fallback..."
  response=$(curl -sL -X POST http://localhost:8080/a2a/ \
    -H "Content-Type: application/json" \
    -H "X-Api-Key: test-api-key" \
    -d '{
      "jsonrpc": "2.0",
      "method": "message/stream",
      "params": {
        "message": {
          "messageId": "test-2-'$(date +%s)'",
          "role": "user",
          "parts": [{"partId": "p1", "text": "list pods in kubently namespace in kind cluster"}]
        }
      },
      "id": 2
    }' 2>/dev/null)

  # Extract text from either status-update or artifact-update
  status_text=$(echo "$response" | grep '"status-update"' | grep -o '"text":"[^"]*"' | cut -d'"' -f4 | tail -1)
  artifact_text=$(echo "$response" | grep '"artifact-update"' | grep -o '"text":"[^"]*"' | cut -d'"' -f4 | tail -1)

  # Check both possible locations for pod information
  if [[ $status_text == *"kubently-api"* ]] || [[ $artifact_text == *"kubently-api"* ]] || 
     [[ $status_text == *"kubently-executor"* ]] || [[ $artifact_text == *"kubently-executor"* ]] || 
     [[ $status_text == *"Running"* ]] || [[ $artifact_text == *"Running"* ]]; then
    echo -e "${GREEN}✅ Test 2 passed: Agent returns pod information${NC}"
  else
    echo -e "${RED}❌ Test 2 failed: Unexpected response${NC}"
    echo "Status text: ${status_text:0:200}"
    echo "Artifact text: ${artifact_text:0:200}"
    exit 1
  fi
fi

# Test 3: Invalid API key
echo -e "\n${YELLOW}Test 3: Authentication with invalid API key${NC}"
response=$(curl -sL -X POST http://localhost:8080/a2a/ \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: invalid-key" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/stream",
    "params": {
      "message": {
        "messageId": "test-3",
        "role": "user",
        "parts": [{"partId": "p1", "text": "test"}]
      }
    },
    "id": 3
  }' 2>/dev/null)

if [[ $response == *"Invalid API key"* ]] || [[ $response == *"Unauthorized"* ]]; then
  echo -e "${GREEN}✅ Test 3 passed: Authentication properly rejects invalid key${NC}"
else
  echo -e "${RED}❌ Test 3 failed: Authentication not working properly${NC}"
  echo "Response: $response"
  exit 1
fi

echo -e "\n${GREEN}✅ All tests passed!${NC}"
echo -e "${YELLOW}Tip: You can also test interactively with:${NC}"
echo "  kubently --api-url localhost:8080 --api-key test-api-key debug"