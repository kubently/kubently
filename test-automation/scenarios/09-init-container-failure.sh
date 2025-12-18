#!/bin/bash

# SCENARIO: Init Container Failure
# SYMPTOM: Pod stuck in Init:0/1 or Init:Error state
# THE FIX: Fix the init container's command or dependencies

# Allow context override: KUBE_CONTEXT=kind-kind-exec-only ./09-init-container-failure.sh setup
KUBE_CONTEXT="${KUBE_CONTEXT:-kind-kubently}"
NAMESPACE="test-scenario-09"

if [ "$1" = "setup" ]; then
    echo "Setting up scenario 09 on context: $KUBE_CONTEXT..."

    kubectl --context $KUBE_CONTEXT create namespace $NAMESPACE --dry-run=client -o yaml | kubectl --context $KUBE_CONTEXT apply -f -

    # Create a deployment with a failing init container
    cat <<EOF | kubectl --context $KUBE_CONTEXT apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: order-processor
  namespace: $NAMESPACE
spec:
  replicas: 2
  selector:
    matchLabels:
      app: orders
  template:
    metadata:
      labels:
        app: orders
    spec:
      initContainers:
      - name: init-database
        image: busybox:1.35
        command: ['sh', '-c']
        args:
        - |
          echo "Waiting for database to be ready..."
          until nslookup database-service.${NAMESPACE}.svc.cluster.local; do
            echo "Waiting for database service..."
            sleep 2
          done
          echo "Database is ready!"
      - name: init-config
        image: busybox:1.35
        command: ['sh', '-c']
        args:
        - |
          echo "Loading configuration..."
          sleep 2
          echo "Configuration loaded!"
      containers:
      - name: main-app
        image: nginx:alpine
        ports:
        - containerPort: 80
        env:
        - name: DB_HOST
          value: "database-service"
EOF

    # Create a service for the deployment (but NOT the database service the init container needs)
    cat <<EOF | kubectl --context $KUBE_CONTEXT apply -f -
apiVersion: v1
kind: Service
metadata:
  name: order-service
  namespace: $NAMESPACE
spec:
  selector:
    app: orders
  ports:
  - port: 80
    targetPort: 80
EOF

    # Wait a moment for pods to attempt initialization
    sleep 5

    # Show the problem
    echo "=== Pods stuck in Init state ==="
    kubectl --context $KUBE_CONTEXT get pods -n $NAMESPACE

    echo ""
    echo "=== Init container logs showing DNS failure ==="
    POD=$(kubectl --context $KUBE_CONTEXT get pods -n $NAMESPACE -o jsonpath='{.items[0].metadata.name}')
    kubectl --context $KUBE_CONTEXT logs $POD -c init-database -n $NAMESPACE --tail=5 || true

    exit 0
fi

if [ "$1" = "cleanup" ]; then
    echo "Cleaning up scenario 09 on context: $KUBE_CONTEXT..."
    kubectl --context $KUBE_CONTEXT delete namespace $NAMESPACE --ignore-not-found=true
    exit 0
fi

# If no argument provided, show usage
echo "Usage: $0 [setup|cleanup]"
exit 1