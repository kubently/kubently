#!/bin/bash
# Deploy and test Kubently in kind cluster

set -e

echo "Kubently Kind Deployment Script"
echo "================================"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
CLUSTER_NAME="${KIND_CLUSTER:-kubently-e2e}"  # Use existing e2e cluster
NAMESPACE="${NAMESPACE:-kubently}"
BUILD_IMAGES="${BUILD_IMAGES:-true}"

# Function to check if command exists
check_command() {
    if ! command -v $1 &> /dev/null; then
        echo -e "${RED}✗${NC} $1 is not installed. Please install it first."
        exit 1
    fi
}

# Function to print status
print_status() {
    echo -e "${GREEN}►${NC} $1"
}

# Check prerequisites
echo "Checking prerequisites..."
check_command docker
check_command kind
check_command kubectl

# Check if cluster exists
if ! kind get clusters | grep -q "^${CLUSTER_NAME}$"; then
    echo -e "${RED}✗${NC} Cluster '${CLUSTER_NAME}' not found!"
    echo "Available clusters:"
    kind get clusters
    echo ""
    echo "To create the cluster, run:"
    echo "  kind create cluster --config deployment/kind-config.yaml"
    exit 1
else
    print_status "Using existing kind cluster: ${CLUSTER_NAME}"
fi

# Set kubectl context
kubectl cluster-info --context kind-${CLUSTER_NAME}

# Build Docker images if requested
if [ "$BUILD_IMAGES" = "true" ]; then
    print_status "Building Docker images..."
    
    # Build API image (includes A2A)
    docker build -f deployment/docker/api/Dockerfile -t kubently-api:latest .
    
    # Build Agent image
    docker build -f deployment/docker/agent/Dockerfile -t kubently-agent:latest .
    
    print_status "Loading images into kind cluster..."
    kind load docker-image kubently-api:latest --name ${CLUSTER_NAME}
    kind load docker-image kubently-agent:latest --name ${CLUSTER_NAME}
else
    print_status "Skipping image build (BUILD_IMAGES=false)"
fi

# Create namespace
print_status "Creating namespace: ${NAMESPACE}"
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -

# Deploy Redis
print_status "Deploying Redis..."
kubectl apply -n ${NAMESPACE} -f - << 'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis
spec:
  replicas: 1
  selector:
    matchLabels:
      app: redis
  template:
    metadata:
      labels:
        app: redis
    spec:
      containers:
      - name: redis
        image: redis:7-alpine
        ports:
        - containerPort: 6379
        resources:
          requests:
            memory: "128Mi"
            cpu: "100m"
          limits:
            memory: "256Mi"
            cpu: "200m"
---
apiVersion: v1
kind: Service
metadata:
  name: redis
spec:
  type: NodePort
  selector:
    app: redis
  ports:
  - port: 6379
    targetPort: 6379
    nodePort: 30379
EOF

# Wait for Redis to be ready
print_status "Waiting for Redis to be ready..."
kubectl wait --for=condition=available --timeout=60s deployment/redis -n ${NAMESPACE}

# Create API key secret
print_status "Creating secrets..."
kubectl create secret generic kubently-secrets \
  --from-literal=api-key=test-api-key \
  --from-literal=agent-token=test-agent-token \
  --namespace=${NAMESPACE} \
  --dry-run=client -o yaml | kubectl apply -f -

# Deploy Kubently API (with A2A)
print_status "Deploying Kubently API..."
kubectl apply -n ${NAMESPACE} -f - << 'EOF'
apiVersion: v1
kind: ServiceAccount
metadata:
  name: kubently-api
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kubently-reader
rules:
- apiGroups: ["*"]
  resources: ["*"]
  verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: kubently-reader-binding
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: kubently-reader
subjects:
- kind: ServiceAccount
  name: kubently-api
  namespace: kubently
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kubently-api
spec:
  replicas: 1
  selector:
    matchLabels:
      app: kubently-api
  template:
    metadata:
      labels:
        app: kubently-api
    spec:
      serviceAccountName: kubently-api
      containers:
      - name: api
        image: kubently-api:latest
        imagePullPolicy: Never
        ports:
        - name: api
          containerPort: 8080
        - name: a2a
          containerPort: 8000
        env:
        - name: REDIS_URL
          value: "redis://redis:6379"
        - name: API_KEYS
          value: "test-api-key"
        - name: LOG_LEVEL
          value: "INFO"
        - name: PORT
          value: "8080"
        - name: A2A_ENABLED
          value: "true"
        - name: AGENT_TOKEN_KIND
          value: "test-agent-token"
        resources:
          requests:
            memory: "256Mi"
            cpu: "100m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /health
            port: api
          initialDelaySeconds: 10
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /health
            port: api
          initialDelaySeconds: 5
          periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: kubently-api
spec:
  type: NodePort
  selector:
    app: kubently-api
  ports:
  - name: api
    port: 8080
    targetPort: 8080
    nodePort: 30500
  - name: a2a
    port: 8000
    targetPort: 8000
    nodePort: 30800
EOF

# Deploy Kubently Agent
print_status "Deploying Kubently Agent..."
kubectl apply -n ${NAMESPACE} -f - << 'EOF'
apiVersion: v1
kind: ServiceAccount
metadata:
  name: kubently-agent
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kubently-agent
rules:
- apiGroups: ["*"]
  resources: ["*"]
  verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: kubently-agent-binding
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: kubently-agent
subjects:
- kind: ServiceAccount
  name: kubently-agent
  namespace: kubently
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kubently-agent
spec:
  replicas: 1
  selector:
    matchLabels:
      app: kubently-agent
  template:
    metadata:
      labels:
        app: kubently-agent
    spec:
      serviceAccountName: kubently-agent
      containers:
      - name: agent
        image: kubently-agent:latest
        imagePullPolicy: Never
        env:
        - name: KUBENTLY_API_URL
          value: "http://kubently-api:8080"
        - name: CLUSTER_ID
          value: "kind"
        - name: KUBENTLY_TOKEN
          value: "test-agent-token"
        - name: LOG_LEVEL
          value: "INFO"
        resources:
          requests:
            memory: "128Mi"
            cpu: "50m"
          limits:
            memory: "256Mi"
            cpu: "200m"
EOF

# Wait for deployments to be ready
print_status "Waiting for deployments to be ready..."
kubectl wait --for=condition=available --timeout=120s deployment/kubently-api -n ${NAMESPACE}
kubectl wait --for=condition=available --timeout=60s deployment/kubently-agent -n ${NAMESPACE}

# Create test workload for debugging
print_status "Creating test workload..."
kubectl apply -f - << 'EOF'
apiVersion: v1
kind: Namespace
metadata:
  name: test-app
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: nginx-test
  namespace: test-app
spec:
  replicas: 2
  selector:
    matchLabels:
      app: nginx
  template:
    metadata:
      labels:
        app: nginx
    spec:
      containers:
      - name: nginx
        image: nginx:alpine
        ports:
        - containerPort: 80
        resources:
          requests:
            memory: "64Mi"
            cpu: "50m"
---
apiVersion: v1
kind: Service
metadata:
  name: nginx-service
  namespace: test-app
spec:
  selector:
    app: nginx
  ports:
  - port: 80
    targetPort: 80
EOF

# Start port-forward for easy access
print_status "Starting port-forward for API access..."
echo "Run this in a separate terminal (or background):"
echo "  kubectl port-forward -n ${NAMESPACE} svc/kubently-api 8080:8080 8000:8000"
echo ""

# Try to start port-forward in background
kubectl port-forward -n ${NAMESPACE} svc/kubently-api 8080:8080 8000:8000 > /dev/null 2>&1 &
PF_PID=$!
echo "Port-forward started in background (PID: $PF_PID)"
echo ""

echo "================================"
echo -e "${GREEN}✓${NC} Kubently deployed successfully!"
echo ""
echo "Access points (via port-forward):"
echo "  Main API:    http://localhost:8080"
echo "  A2A Server:  http://localhost:8000"
echo ""
echo "Test the deployment:"
echo ""
echo "1. Test REST API:"
echo "   curl -H 'Authorization: Bearer test-api-key' http://localhost:8080/health"
echo ""
echo "2. Create a debug session:"
echo "   curl -X POST http://localhost:8080/debug/session \\"
echo "     -H 'Authorization: Bearer test-api-key' \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"cluster_id\": \"kind\"}'"
echo ""
echo "3. Execute a kubectl command:"
echo "   curl -X POST http://localhost:8080/debug/execute \\"
echo "     -H 'Authorization: Bearer test-api-key' \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"cluster_id\": \"kind\", \"command_type\": \"get\", \"args\": [\"pods\", \"-A\"]}'"
echo ""
echo "4. Test A2A interface:"
echo "   curl http://localhost:8000/"
echo ""
echo "5. Use A2A with curl:"
echo "   curl -X POST http://localhost:8080/a2a/ \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -H 'x-api-key: test-api-key' \\"
echo "     -d @docs/test-queries/simple-test.json"
echo ""
echo "To stop port-forward:"
echo "  kill $PF_PID"
echo ""
echo "To clean up Kubently (keeping cluster):"
echo "  kubectl delete namespace ${NAMESPACE}"