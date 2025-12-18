# Setting Up Agentgateway in Front of Kubently

This guide covers deploying [kgateway/agentgateway](https://kgateway.dev/docs/agentgateway/latest/) as an API gateway in front of Kubently, enabling centralized authentication, A2A protocol handling, and traffic management for your AI agent infrastructure.

## Overview

**Agentgateway** is an open-source, Kubernetes-native gateway specifically designed for AI agent connectivity. It provides:

- **A2A Protocol Support**: Native understanding of the Agent-to-Agent (A2A) protocol
- **MCP Integration**: Model Context Protocol tool routing
- **Traffic Management**: Load balancing, retries, timeouts for agent traffic
- **Security**: Centralized authentication, TLS termination, CORS handling

### Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   AI Client     │────▶│   Agentgateway   │────▶│    Kubently     │
│  (Claude, etc)  │     │   (Port 8080)    │     │   (Port 8080)   │
└─────────────────┘     └──────────────────┘     └─────────────────┘
        │                       │
        │  POST /a2a/           │  Pass-through
        │  (no auth header)     │  + inject X-API-Key
        ▼                       ▼
```

**Key benefits:**
- Clients don't need to manage API keys - the gateway injects them
- A2A protocol-aware routing with proper SSE streaming support
- Single entry point for multiple AI agent backends

## Prerequisites

- Kubernetes cluster (1.25+)
- `kubectl` configured with cluster access
- `helm` v3.x installed
- Kubently deployed and running

## Step 1: Install Gateway API CRDs

The Kubernetes Gateway API provides the foundational Custom Resource Definitions (CRDs) that agentgateway extends.

```bash
# Install the standard Gateway API CRDs (v1.4.0)
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.0/standard-install.yaml
```

**Verify installation:**

```bash
kubectl get crds | grep gateway
```

Expected output:
```
gatewayclasses.gateway.networking.k8s.io
gateways.gateway.networking.k8s.io
httproutes.gateway.networking.k8s.io
referencegrants.gateway.networking.k8s.io
```

## Step 2: Install kgateway with Agentgateway Enabled

kgateway is the control plane that manages agentgateway proxies. Install it with the agentgateway feature flag enabled.

```bash
# Install kgateway with agentgateway enabled
helm upgrade --install kgateway oci://ghcr.io/kgateway-dev/kgateway-helm \
  --namespace kgateway-system \
  --create-namespace \
  --set agentgateway.enabled=true
```

**Verify installation:**

```bash
# Check kgateway controller is running
kubectl get pods -n kgateway-system

# Verify the agentgateway GatewayClass exists
kubectl get gatewayclass agentgateway
```

Expected output:
```
NAME           CONTROLLER                    ACCEPTED   AGE
agentgateway   kgateway.dev/agentgateway     True       1m
```

## Step 3: Configure the Backend Service for A2A Protocol

Agentgateway uses the Kubernetes `appProtocol` field on Services to identify A2A backends. This enables protocol-aware routing and proper handling of A2A's JSON-RPC over SSE communication pattern.

**Option A: Patch existing service**

```bash
kubectl patch svc kubently-api -n kubently --type='json' -p='[
  {"op": "add", "path": "/spec/ports/0/appProtocol", "value": "kgateway.dev/a2a"}
]'
```

**Option B: Update Helm values** (recommended for production)

Add to your Kubently `values.yaml`:

```yaml
api:
  service:
    ports:
      - name: http
        port: 8080
        targetPort: 8080
        appProtocol: kgateway.dev/a2a
```

**Verify the service configuration:**

```bash
kubectl get svc kubently-api -n kubently -o jsonpath='{.spec.ports[0].appProtocol}'
```

Expected output:
```
kgateway.dev/a2a
```

## Step 4: Create the Gateway Resource

The Gateway resource defines the network entrypoint - the listener configuration that accepts incoming traffic.

```yaml
# gateway.yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway
  namespace: kgateway-system
  labels:
    app: agentgateway
spec:
  gatewayClassName: agentgateway
  listeners:
    - name: http
      protocol: HTTP
      port: 8080
      allowedRoutes:
        namespaces:
          from: All  # Allow HTTPRoutes from any namespace
```

**Apply:**

```bash
kubectl apply -f gateway.yaml
```

**Key configuration details:**

| Field | Value | Description |
|-------|-------|-------------|
| `gatewayClassName` | `agentgateway` | References the agentgateway proxy template |
| `listeners[].port` | `8080` | External port the gateway listens on |
| `listeners[].protocol` | `HTTP` | Protocol for this listener |
| `allowedRoutes.namespaces.from` | `All` | Permits routes from any namespace to attach |

**Verify Gateway is programmed:**

```bash
kubectl get gateway agentgateway -n kgateway-system
```

Expected output:
```
NAME           CLASS          ADDRESS         PROGRAMMED   AGE
agentgateway   agentgateway   <IP or pending> True         1m
```

## Step 5: Create the HTTPRoute

The HTTPRoute defines how traffic flows from the Gateway to your backend service. This configuration:
- Routes `/a2a/*` requests to Kubently
- Automatically injects the API key header (so clients don't need it)

```yaml
# httproute.yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: kubently-route
  namespace: kubently
spec:
  parentRefs:
    - name: agentgateway
      namespace: kgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /a2a
      filters:
        # Inject API key header for authentication
        - type: RequestHeaderModifier
          requestHeaderModifier:
            add:
              - name: X-API-Key
                value: "YOUR_KUBENTLY_API_KEY"
      backendRefs:
        - name: kubently-api
          port: 8080
```

> **Note**: Replace `YOUR_KUBENTLY_API_KEY` with your actual Kubently API key.

**Apply:**

```bash
kubectl apply -f httproute.yaml
```

**Key configuration details:**

| Field | Description |
|-------|-------------|
| `parentRefs` | Links this route to the Gateway |
| `matches[].path` | Match traffic with PathPrefix `/a2a` |
| `filters[].RequestHeaderModifier` | Inject the `X-API-Key` header automatically |
| `backendRefs` | Target the kubently-api service on port 8080 |

**Verify HTTPRoute is accepted:**

```bash
kubectl get httproute kubently-route -n kubently
```

Check detailed status:

```bash
kubectl describe httproute kubently-route -n kubently
```

Look for `Accepted: True` and `ResolvedRefs: True` in the status conditions.

## Step 6: Verify End-to-End Connectivity

### Check Gateway Deployment

```bash
# Verify the agentgateway proxy pod is running
kubectl get pods -n kgateway-system -l app.kubernetes.io/name=agentgateway

# Check the gateway service
kubectl get svc -n kgateway-system

# Check attached routes
kubectl get gateway agentgateway -n kgateway-system -o jsonpath='{.status.listeners[0].attachedRoutes}'
# Should output: 1
```

### Get Gateway Address

```bash
# For LoadBalancer type (cloud environments)
GATEWAY_IP=$(kubectl get gateway agentgateway -n kgateway-system -o jsonpath='{.status.addresses[0].value}')
echo "Gateway IP: $GATEWAY_IP"

# For local testing, port-forward
kubectl port-forward -n kgateway-system svc/agentgateway 8080:8080 &
```

### Test A2A Connectivity

```bash
# Test a streaming message through the gateway
# Note: No X-API-Key header needed - the gateway injects it!
curl -s -X POST http://localhost:8080/a2a/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "test-1",
    "method": "message/stream",
    "params": {
      "message": {
        "messageId": "msg-1",
        "role": "user",
        "parts": [{"kind": "text", "text": "hello"}]
      }
    }
  }'
```

Expected response (SSE stream):
```
data: {"id":"test-1","jsonrpc":"2.0","result":{"contextId":"...","kind":"task","status":{"state":"submitted"}}}

data: {"id":"test-1","jsonrpc":"2.0","result":{"kind":"status-update","status":{"state":"working"},...}}
```

### Test with A2A Client

```bash
# Using the agent-chat-cli
uvx https://github.com/cnoe-io/agent-chat-cli.git a2a --host localhost --port 8080 --path /a2a/
```

## Complete Configuration Files

### All-in-One Manifest

```yaml
# agentgateway-kubently.yaml
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway
  namespace: kgateway-system
  labels:
    app: agentgateway
spec:
  gatewayClassName: agentgateway
  listeners:
    - name: http
      protocol: HTTP
      port: 8080
      allowedRoutes:
        namespaces:
          from: All
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: kubently-route
  namespace: kubently
spec:
  parentRefs:
    - name: agentgateway
      namespace: kgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /a2a
      filters:
        - type: RequestHeaderModifier
          requestHeaderModifier:
            add:
              - name: X-API-Key
                value: "YOUR_KUBENTLY_API_KEY"
      backendRefs:
        - name: kubently-api
          port: 8080
```

**Apply all resources:**

```bash
# First, patch the service for A2A protocol
kubectl patch svc kubently-api -n kubently --type='json' -p='[
  {"op": "add", "path": "/spec/ports/0/appProtocol", "value": "kgateway.dev/a2a"}
]'

# Then apply Gateway and HTTPRoute
kubectl apply -f agentgateway-kubently.yaml
```

## Alternative: Path Rewriting

If you want clients to access Kubently at `/` instead of `/a2a/`, you can add URL rewriting. However, be aware this can cause redirect loops if Kubently returns redirects.

```yaml
# httproute-with-rewrite.yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: kubently-route-rewrite
  namespace: kubently
spec:
  parentRefs:
    - name: agentgateway
      namespace: kgateway-system
  rules:
    # Route /kubently/* to /a2a/* on backend
    - matches:
        - path:
            type: PathPrefix
            value: /kubently
      filters:
        - type: RequestHeaderModifier
          requestHeaderModifier:
            add:
              - name: X-API-Key
                value: "YOUR_KUBENTLY_API_KEY"
        - type: URLRewrite
          urlRewrite:
            path:
              type: ReplacePrefixMatch
              replacePrefixMatch: /a2a
      backendRefs:
        - name: kubently-api
          port: 8080
```

> **Warning**: Avoid rewriting from `/` to `/a2a/` as this causes double-rewriting issues when the backend returns redirects.

## Troubleshooting

### Gateway Not Programmed

```bash
# Check Gateway status
kubectl describe gateway agentgateway -n kgateway-system

# Check kgateway controller logs
kubectl logs -n kgateway-system -l app.kubernetes.io/name=kgateway
```

### HTTPRoute Not Accepted

```bash
# Check route status
kubectl describe httproute kubently-route -n kubently

# Common issues:
# - Backend service doesn't exist
# - Port mismatch
# - Namespace permissions (check allowedRoutes)
```

### Connection Refused

```bash
# Verify backend service is running
kubectl get pods -n kubently
kubectl get svc kubently-api -n kubently

# Check agentgateway proxy logs
kubectl logs -n kgateway-system -l app.kubernetes.io/name=agentgateway
```

### A2A Protocol Errors

```bash
# Verify appProtocol is set
kubectl get svc kubently-api -n kubently -o jsonpath='{.spec.ports[0].appProtocol}'
# Should output: kgateway.dev/a2a

# Test direct connectivity (bypassing gateway)
kubectl port-forward -n kubently svc/kubently-api 8081:8080 &
curl -s -X POST http://localhost:8081/a2a/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d '{"jsonrpc":"2.0","id":"1","method":"message/stream","params":{"message":{"messageId":"m1","role":"user","parts":[{"kind":"text","text":"test"}]}}}'
```

### 307 Redirect Loops

If you see 307 redirects, this usually means:
1. The path rewrite is causing double-rewrites
2. Solution: Use `/a2a` path prefix matching without rewriting (as shown in main config)

## Advanced Configuration

### Adding TLS Termination

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway
  namespace: kgateway-system
spec:
  gatewayClassName: agentgateway
  listeners:
    - name: https
      protocol: HTTPS
      port: 443
      tls:
        mode: Terminate
        certificateRefs:
          - name: agentgateway-tls
            kind: Secret
      allowedRoutes:
        namespaces:
          from: All
```

### Multiple Backend Routes

Route different paths to different AI agents:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: multi-agent-route
  namespace: kubently
spec:
  parentRefs:
    - name: agentgateway
      namespace: kgateway-system
  rules:
    # Route /kubently to Kubently (Kubernetes troubleshooting)
    - matches:
        - path:
            type: PathPrefix
            value: /kubently
      filters:
        - type: RequestHeaderModifier
          requestHeaderModifier:
            add:
              - name: X-API-Key
                value: "KUBENTLY_API_KEY"
        - type: URLRewrite
          urlRewrite:
            path:
              type: ReplacePrefixMatch
              replacePrefixMatch: /a2a
      backendRefs:
        - name: kubently-api
          port: 8080
    # Route /datadog-agent to another A2A agent
    - matches:
        - path:
            type: PathPrefix
            value: /datadog-agent
      filters:
        - type: URLRewrite
          urlRewrite:
            path:
              type: ReplacePrefixMatch
              replacePrefixMatch: /
      backendRefs:
        - name: datadog-agent-api
          port: 8080
```

### CORS Configuration

For browser-based AI clients, add CORS headers using a RouteOption (check kgateway docs for exact CRD syntax in your version):

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: RouteOption
metadata:
  name: cors-policy
  namespace: kubently
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: kubently-route
  options:
    cors:
      allowOrigins:
        - "*"
      allowMethods:
        - GET
        - POST
        - OPTIONS
      allowHeaders:
        - "*"
      exposeHeaders:
        - "*"
```

## References

- [Kubernetes Gateway API Specification](https://gateway-api.sigs.k8s.io/)
- [kgateway Documentation](https://kgateway.dev/docs/)
- [Agentgateway Documentation](https://kgateway.dev/docs/agentgateway/latest/)
- [A2A Protocol Specification](https://a2a-protocol.org/latest/)
- [Kubently Documentation](https://github.com/kubently/kubently)
