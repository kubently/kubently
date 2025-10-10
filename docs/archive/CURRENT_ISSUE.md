# Current Status

  The A2A (Agent-to-Agent) protocol implementation for Kubently is partially working. The LangGraph recursion issue has
  been resolved by implementing the response format structure, but the agent fails to execute kubectl tools properly.

  Testing Environment Setup

  1. Prerequisites

- Docker Desktop running
- Kind cluster named kubently-e2e
- kubectl configured
- Environment variables in .env file:
  LLM_PROVIDER=openai
  OPENAI_API_KEY=<your-api-key>
  OPENAI_ENDPOINT=<https://api.openai.com/v1>
  OPENAI_MODEL_NAME=gpt-4o
  A2A_PORT=8000

  2. Build and Deploy

# Build the Docker image

  docker build -f deployment/docker/api/Dockerfile -t kubently-api:latest .

# Load image to kind cluster

  kind load docker-image kubently-api:latest --name kubently-e2e

# Deploy to Kubernetes

  kubectl apply -k deployment/k8s/

# Restart deployment to pick up new image

  kubectl rollout restart deployment/kubently-api -n kubently
  kubectl rollout status deployment/kubently-api -n kubently --timeout=60s

  3. Port Forwarding

# Forward both REST API and A2A ports

  kubectl port-forward -n kubently svc/kubently-api 8000:8000 &  # A2A port
  kubectl port-forward -n kubently svc/kubently-api 8081:8080 &  # REST API port

  4. Verification Tests

  REST API Test (Working):

# Should return: {"status":"healthy"}

  curl <http://localhost:8081/health>

# Should successfully execute kubectl command

  curl -X POST <http://localhost:8081/debug/execute> \
    -H 'X-Api-Key: test-api-key' \
    -H 'Content-Type: application/json' \
    -d '{
      "cluster_id": "kind",
      "command_type": "get",
      "args": ["pods"],
      "namespace": "kubently"
    }'

  A2A Server Test (Partially Working):

# Should return agent card JSON

  curl <http://localhost:8000/.well-known/agent.json>

  The Issue

  Reproduction Steps

# Test with curl (requires x-api-key header)

  curl -X POST http://localhost:8080/a2a/ \
    -H 'Content-Type: application/json' \
    -H 'x-api-key: test-api-key' \
    -d '{"jsonrpc":"2.0","method":"message/stream","params":{"messages":[{"messageId":"msg-001","role":"user","parts":[{"partId":"part-001","text":"what pods are running in the kubently namespace?"}]}]},"id":"test-001"}'

  Expected Behavior

  The agent should:

  1. Receive the natural language query
  2. Decide to call the execute_kubectl tool with arguments ("get", ["pods"], "kubently", "default")
  3. Execute the kubectl command via the REST API
  4. Return a formatted response listing the pods

  Actual Behavior

- The agent connects to OpenAI successfully
- It attempts to execute tools but encounters "argument handling" errors
- It retries repeatedly until hitting the recursion limit (currently set to 10)
- Returns an error message about technical difficulties

  Error Symptoms

  1. Response from A2A client:
  Analysis: I attempted to execute the kubectl command to list pods in the
  'kubently' namespace, but encountered a technical issue with the command
  execution. The error indicates a problem with the argument handling in the
  function call.

  Response: I am currently experiencing technical difficulties in executing
  the command to list the pods in the "kubently" namespace.

  2. In pod logs:
  kubectl logs deployment/kubently-api -n kubently --tail=100
  Shows:

- Multiple OpenAI API calls (10 iterations)
- Deprecation warning about BaseTool.__call__
- GraphRecursionError after 10 attempts

  Key Files to Review

  1. Main Agent Implementation:
  - /Users/adickinson/repos/kubently/kubently/modules/a2a/protocol_bindings/a2a_server/agent.py
  - Line 182: The execute_kubectl tool definition
  - Lines 152-167: Agent creation with LangGraph
  - Lines 418-421: Recursion limit configuration (currently 10 for debugging)
  2. Reference Implementation:
  - /Users/adickinson/repos/mas-agent-backstage/agent_backstage/protocol_bindings/a2a_server/agent.py
  - Shows working pattern for tool definitions and agent setup

  What Has Been Fixed

  1. ✅ Added ResponseFormat class for structured responses
  2. ✅ Added RESPONSE_FORMAT_INSTRUCTION for JSON formatting
  3. ✅ Updated create_react_agent call with response_format parameter
  4. ✅ Changed streaming to stream_mode="values"
  5. ✅ Fixed nested tool calls causing deprecation warnings

  What Still Needs Fixing

  The core issue appears to be a mismatch between:

- How the LLM (GPT-4o) formats tool call arguments
- How the LangChain @tool decorated functions expect arguments

  Possible causes:

  1. Argument Structure: The LLM might be calling execute_kubectl(command="get pods -n kubently") instead of
  execute_kubectl(command="get", args=["pods"], namespace="kubently")
  2. Async/Await Issues: Tool execution might be failing due to async handling
  3. Type Annotations: LangChain tools might need different type hints
  4. Tool Description: The docstring might not be clear enough for the LLM

  Debugging Suggestions

  1. Enable debug logging to see exact tool calls:
  export A2A_SERVER_DEBUG=true
  2. Add logging to see what arguments the LLM is passing:
  @tool
  async def execute_kubectl(command: str, args: list[str], ...):
      logger.info(f"Tool called with: command={command}, args={args}, ...")
  3. Test with a simpler tool first to isolate the issue:
  @tool
  async def simple_test(message: str) -> str:
      """Test tool that just echoes the message."""
      return f"Echo: {message}"
  4. Check LangGraph's tool calling format requirements and compare with mas-agent-backstage

  Success Criteria

  When fixed, running the A2A client with "what pods are running in the kubently namespace?" should return something
  like:
  Analysis: I need to check what pods are running in the kubently namespace.
  I'll execute kubectl get pods command to retrieve this information.

  Response: Here are the pods running in the kubently namespace:

- kubently-api-75bfbbcd64-9sq9p (Running)
- kubently-agent-5c5c8b9c79-5vrgk (Running)
- redis-66c795ddf-knl9g (Running)

  All pods are in a healthy Running state.
