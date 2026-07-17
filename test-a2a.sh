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

# Test 3: Multi-turn conversation with cluster selected
# Regression test: with a cluster selected (A2A metadata clusterId), the per-turn
# cluster-context injection used to be a system-role message; combined with the
# LangGraph checkpointer history the second turn always failed with
# "Received multiple non-consecutive system messages" (fixed in v2.3.5+ by
# injecting the context as a user-role message).
echo -e "\n${YELLOW}Test 3: Multi-turn conversation on same context (cluster selected)${NC}"

if check_a2a_client; then
  # Use Python A2A test client
  echo "Using Python A2A test client..."
  response1=$(python3 test-automation/a2a_test_client.py \
    "http://localhost:8080" \
    "test-api-key" \
    "list pods in the kubently namespace" \
    "" \
    "kind" 2>/dev/null)

  context_id=$(echo "$response1" | python3 -c "import sys, json; print(json.load(sys.stdin).get('context_id') or '')")

  if [ -z "$context_id" ]; then
    echo -e "${RED}❌ Test 3 failed: No contextId returned from first message${NC}"
    echo "Response: $(echo "$response1" | python3 -c "import sys, json; print(json.load(sys.stdin).get('final_text', ''))")"
    exit 1
  fi
  echo "First turn OK (contextId: $context_id), sending follow-up on same context..."

  response2=$(python3 test-automation/a2a_test_client.py \
    "http://localhost:8080" \
    "test-api-key" \
    "now show me the services in that namespace" \
    "$context_id" \
    "kind" 2>/dev/null)

  status2=$(echo "$response2" | python3 -c "import sys, json; print(json.load(sys.stdin).get('status', ''))")
  final_text2=$(echo "$response2" | python3 -c "import sys, json; print(json.load(sys.stdin).get('final_text', ''))")

  if [ "$status2" != "success" ]; then
    echo -e "${RED}❌ Test 3 failed: A2A client error on follow-up turn${NC}"
    echo "Response: $response2"
    exit 1
  elif [[ -z "$final_text2" ]] || [[ $final_text2 == *"I encountered an error"* ]] || [[ $final_text2 == *"non-consecutive"* ]]; then
    echo -e "${RED}❌ Test 3 failed: Follow-up turn returned an error (multi-turn regression)${NC}"
    echo "Response: ${final_text2:0:300}"
    exit 1
  else
    echo -e "${GREEN}✅ Test 3 passed: Follow-up turn on same context succeeded${NC}"
  fi
else
  # Fallback to curl
  echo "A2A Python client not available, using curl fallback..."
  response1=$(curl -sL -X POST http://localhost:8080/a2a/ \
    -H "Content-Type: application/json" \
    -H "X-Api-Key: test-api-key" \
    -d '{
      "jsonrpc": "2.0",
      "method": "message/stream",
      "params": {
        "message": {
          "messageId": "test-3a-'$(date +%s)'",
          "role": "user",
          "parts": [{"partId": "p1", "text": "list pods in the kubently namespace"}]
        },
        "metadata": {"clusterId": "kind"}
      },
      "id": 3
    }' 2>/dev/null)

  context_id=$(echo "$response1" | grep -o '"contextId":"[^"]*"' | head -1 | cut -d'"' -f4)

  if [ -z "$context_id" ]; then
    echo -e "${RED}❌ Test 3 failed: No contextId returned from first message${NC}"
    echo "Response: ${response1:0:300}"
    exit 1
  fi
  echo "First turn OK (contextId: $context_id), sending follow-up on same context..."

  response2=$(curl -sL -X POST http://localhost:8080/a2a/ \
    -H "Content-Type: application/json" \
    -H "X-Api-Key: test-api-key" \
    -d '{
      "jsonrpc": "2.0",
      "method": "message/stream",
      "params": {
        "message": {
          "messageId": "test-3b-'$(date +%s)'",
          "role": "user",
          "parts": [{"partId": "p1", "text": "now show me the services in that namespace"}],
          "contextId": "'$context_id'"
        },
        "metadata": {"clusterId": "kind"}
      },
      "id": 4
    }' 2>/dev/null)

  status_text=$(echo "$response2" | grep '"status-update"' | grep -o '"text":"[^"]*"' | cut -d'"' -f4 | tail -1)
  artifact_text=$(echo "$response2" | grep '"artifact-update"' | grep -o '"text":"[^"]*"' | cut -d'"' -f4 | tail -1)

  if [[ $response2 == *"I encountered an error"* ]] || [[ $response2 == *"non-consecutive"* ]]; then
    echo -e "${RED}❌ Test 3 failed: Follow-up turn returned an error (multi-turn regression)${NC}"
    echo "Status text: ${status_text:0:300}"
    echo "Artifact text: ${artifact_text:0:300}"
    exit 1
  elif [ -z "$status_text" ] && [ -z "$artifact_text" ]; then
    echo -e "${RED}❌ Test 3 failed: Follow-up turn returned no response text${NC}"
    echo "Response: ${response2:0:300}"
    exit 1
  else
    echo -e "${GREEN}✅ Test 3 passed: Follow-up turn on same context succeeded${NC}"
  fi
fi

# Test 4: Invalid API key
echo -e "\n${YELLOW}Test 4: Authentication with invalid API key${NC}"
response=$(curl -sL -X POST http://localhost:8080/a2a/ \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: invalid-key" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/stream",
    "params": {
      "message": {
        "messageId": "test-4",
        "role": "user",
        "parts": [{"partId": "p1", "text": "test"}]
      }
    },
    "id": 5
  }' 2>/dev/null)

if [[ $response == *"Invalid API key"* ]] || [[ $response == *"Unauthorized"* ]]; then
  echo -e "${GREEN}✅ Test 4 passed: Authentication properly rejects invalid key${NC}"
else
  echo -e "${RED}❌ Test 4 failed: Authentication not working properly${NC}"
  echo "Response: $response"
  exit 1
fi

echo -e "\n${GREEN}✅ All tests passed!${NC}"
echo -e "${YELLOW}Tip: You can also test interactively with:${NC}"
echo "  kubently --api-url localhost:8080 --api-key test-api-key debug"