#!/bin/bash

# SCENARIO: Failed Readiness Probe
# SYMPTOM: Pod Running but never becomes Ready (0/1), Service has no endpoints
# THE FIX: Fix readiness probe path to '/' or remove the readiness probe


NAMESPACE="test-scenario-07"

if [ "$1" = "setup" ]; then
    echo "Setting up scenario 07..."
    
    kubectl --context kind-kubently create namespace $NAMESPACE --dry-run=client -o yaml | kubectl --context kind-kubently apply -f -
    cat <<EOF | kubectl --context kind-kubently apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: readiness-failure-deployment
  namespace: $NAMESPACE
spec:
  replicas: 1
  selector:
    matchLabels:
      app: readiness-app
  template:
    metadata:
      labels:
        app: readiness-app
    spec:
      containers:
      - name: nginx
        image: nginx:latest
        ports:
        - containerPort: 80
        readinessProbe:
          httpGet:
            path: /ready
            port: 80
          initialDelaySeconds: 5
          periodSeconds: 3
---
apiVersion: v1
kind: Service
metadata:
  name: readiness-service
  namespace: $NAMESPACE
spec:
  selector:
    app: readiness-app
  ports:
  - port: 80
    targetPort: 80
EOF
    kubectl --context kind-kubently get pods -n $NAMESPACE
    kubectl --context kind-kubently get endpoints -n $NAMESPACE
    
    # Wait for resources to be ready
    sleep 5
    
    exit 0
fi

if [ "$1" = "cleanup" ]; then
    echo "Cleaning up scenario 07..."
    kubectl --context kind-kubently delete namespace $NAMESPACE --ignore-not-found=true
    exit 0
fi

# If no argument provided, show usage
echo "Usage: $0 [setup|cleanup]"
exit 1