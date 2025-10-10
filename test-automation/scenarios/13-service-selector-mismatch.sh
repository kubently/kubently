#!/bin/bash

# SCENARIO: Service Selector Mismatch
# SYMPTOM: Service has 0 endpoints, cannot connect to service
# Expected fix: Update service selector to match pod labels

NAMESPACE="test-scenario-13"

if [ "$1" = "setup" ]; then
    echo "Setting up scenario 13..."
    
    kubectl --context kind-kubently create namespace $NAMESPACE --dry-run=client -o yaml | kubectl --context kind-kubently apply -f -
    
    cat <<EOF | kubectl --context kind-kubently apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app-deployment
  namespace: $NAMESPACE
spec:
  replicas: 2
  selector:
    matchLabels:
      app: my-app
  template:
    metadata:
      labels:
        app: my-app
    spec:
      containers:
      - name: nginx
        image: nginx:latest
        ports:
        - containerPort: 80
---
apiVersion: v1
kind: Service
metadata:
  name: app-service
  namespace: $NAMESPACE
spec:
  selector:
    app: wrong-label  # Intentional mismatch
  ports:
  - port: 80
    targetPort: 80
  type: ClusterIP
EOF
    
    # Wait for pods to be ready
    kubectl --context kind-kubently wait --for=condition=ready pod -l app=my-app -n $NAMESPACE --timeout=60s
    
    exit 0
fi

if [ "$1" = "cleanup" ]; then
    echo "Cleaning up scenario 13..."
    kubectl --context kind-kubently delete namespace $NAMESPACE --ignore-not-found=true
    exit 0
fi

# If no argument provided, show usage
echo "Usage: $0 [setup|cleanup]"
exit 1