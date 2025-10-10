export function generateAgentDeployment(
  clusterId: string,
  token: string,
  apiUrl: string,
  namespace = 'kubently',
  image = 'kubently/agent:latest'
): string {
  return `apiVersion: v1
kind: Namespace
metadata:
  name: ${namespace}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: kubently-agent
  namespace: ${namespace}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kubently-agent
rules:
- apiGroups: ["*"]
  resources: ["*"]
  verbs: ["*"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: kubently-agent
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: kubently-agent
subjects:
- kind: ServiceAccount
  name: kubently-agent
  namespace: ${namespace}
---
apiVersion: v1
kind: Secret
metadata:
  name: kubently-agent-token
  namespace: ${namespace}
type: Opaque
stringData:
  token: ${token}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kubently-agent
  namespace: ${namespace}
  labels:
    app: kubently-agent
    cluster: ${clusterId}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: kubently-agent
  template:
    metadata:
      labels:
        app: kubently-agent
        cluster: ${clusterId}
    spec:
      serviceAccountName: kubently-agent
      containers:
      - name: agent
        image: ${image}
        imagePullPolicy: Always
        env:
        - name: KUBENTLY_API_URL
          value: ${apiUrl}
        - name: KUBENTLY_CLUSTER_ID
          value: ${clusterId}
        - name: KUBENTLY_TOKEN
          valueFrom:
            secretKeyRef:
              name: kubently-agent-token
              key: token
        - name: KUBENTLY_DEBUG
          value: "false"
        - name: KUBENTLY_RECONNECT_INTERVAL
          value: "30"
        - name: KUBENTLY_MAX_RECONNECTS
          value: "10"
        resources:
          requests:
            memory: "128Mi"
            cpu: "100m"
          limits:
            memory: "256Mi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 10
          periodSeconds: 30
          timeoutSeconds: 5
          failureThreshold: 3
        readinessProbe:
          httpGet:
            path: /ready
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 10
          timeoutSeconds: 3
          failureThreshold: 3
        ports:
        - name: http
          containerPort: 8080
          protocol: TCP`;
}

export function generateDockerCompose(
  clusterId: string,
  token: string,
  apiUrl: string,
  kubeconfigPath?: string
): string {
  const kubeconfig = kubeconfigPath || '${HOME}/.kube/config';
  
  return `version: '3.8'

services:
  kubently-agent:
    image: kubently/agent:latest
    container_name: kubently-agent-${clusterId}
    environment:
      - KUBENTLY_API_URL=${apiUrl}
      - KUBENTLY_CLUSTER_ID=${clusterId}
      - KUBENTLY_TOKEN=${token}
      - KUBENTLY_DEBUG=true
      - KUBENTLY_RECONNECT_INTERVAL=30
      - KUBENTLY_MAX_RECONNECTS=10
      - KUBECONFIG=/root/.kube/config
    volumes:
      - ${kubeconfig}:/root/.kube/config:ro
    restart: unless-stopped
    networks:
      - kubently

networks:
  kubently:
    name: kubently-network
    driver: bridge`;
}

export function generateHelmValues(
  clusterId: string,
  token: string,
  apiUrl: string,
  namespace = 'kubently',
  image = 'kubently/agent:latest'
): string {
  const [repository, tag] = image.includes(':') 
    ? image.split(':') 
    : [image, 'latest'];
  
  return `# Kubently Agent Helm Values
# Generated for cluster: ${clusterId}

agent:
  enabled: true
  
  image:
    repository: ${repository}
    tag: ${tag}
    pullPolicy: Always
  
  config:
    apiUrl: ${apiUrl}
    clusterId: ${clusterId}
    token: ${token}
    debug: false
    reconnectInterval: 30
    maxReconnects: 10
  
  resources:
    requests:
      memory: "128Mi"
      cpu: "100m"
    limits:
      memory: "256Mi"
      cpu: "500m"
  
  serviceAccount:
    create: true
    name: kubently-agent
  
  rbac:
    create: true
    clusterRole: true
  
  probes:
    liveness:
      enabled: true
      initialDelaySeconds: 10
      periodSeconds: 30
      timeoutSeconds: 5
      failureThreshold: 3
    
    readiness:
      enabled: true
      initialDelaySeconds: 5
      periodSeconds: 10
      timeoutSeconds: 3
      failureThreshold: 3

namespace: ${namespace}

# Optional: Node selector
nodeSelector: {}

# Optional: Tolerations
tolerations: []

# Optional: Affinity rules
affinity: {}`;
}

export function generateShellScript(
  clusterId: string,
  token: string,
  apiUrl: string,
  namespace = 'kubently'
): string {
  const manifest = generateAgentDeployment(clusterId, token, apiUrl, namespace);
  
  return `#!/bin/bash
# Kubently Agent Deployment Script
# Generated for cluster: ${clusterId}

set -e

CLUSTER_ID="${clusterId}"
NAMESPACE="${namespace}"
API_URL="${apiUrl}"
TOKEN="${token}"

echo "Deploying Kubently agent to cluster: $CLUSTER_ID"
echo "Namespace: $NAMESPACE"
echo "API URL: $API_URL"

# Check kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo "Error: kubectl not found. Please install kubectl first."
    exit 1
fi

# Check cluster connection
if ! kubectl cluster-info &> /dev/null; then
    echo "Error: Cannot connect to Kubernetes cluster."
    echo "Please ensure your kubeconfig is properly configured."
    exit 1
fi

# Create namespace if it doesn't exist
kubectl create namespace $NAMESPACE --dry-run=client -o yaml | kubectl apply -f -

# Deploy agent
cat <<'EOF' | kubectl apply -f -
${manifest}
EOF

echo ""
echo "Deployment complete!"
echo ""
echo "Check agent status with:"
echo "  kubectl -n $NAMESPACE get pods"
echo ""
echo "View agent logs with:"
echo "  kubectl -n $NAMESPACE logs -l app=kubently-agent"
echo ""
echo "Remove agent with:"
echo "  kubectl -n $NAMESPACE delete deployment kubently-agent"
echo "  kubectl delete namespace $NAMESPACE"`;
}