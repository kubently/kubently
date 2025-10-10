#!/bin/bash

# SCENARIO: Missing ServiceAccount
# SYMPTOM: Pod fails to start with "serviceaccount not found" error
# THE FIX: Create the missing ServiceAccount 'custom-sa' or use 'default'


NAMESPACE="test-scenario-18"

if [ "$1" = "setup" ]; then
    echo "Setting up scenario 18..."
    
    kubectl --context kind-kubently create namespace $NAMESPACE --dry-run=client -o yaml | kubectl --context kind-kubently apply -f -
    cat <<EOF | kubectl --context kind-kubently apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: missing-sa-deployment
  namespace: $NAMESPACE
spec:
  replicas: 1
  selector:
    matchLabels:
      app: sa-app
  template:
    metadata:
      labels:
        app: sa-app
    spec:
      serviceAccountName: custom-sa
      containers:
      - name: app
        image: busybox:latest
        command: ["sh", "-c", "sleep 3600"]
EOF
    kubectl --context kind-kubently get pods -n $NAMESPACE
    kubectl --context kind-kubently describe pod -n $NAMESPACE | grep -A 10 Events
    
    # Wait for resources to be ready
    sleep 5
    
    exit 0
fi

if [ "$1" = "cleanup" ]; then
    echo "Cleaning up scenario 18..."
    kubectl --context kind-kubently delete namespace $NAMESPACE --ignore-not-found=true
    exit 0
fi

# If no argument provided, show usage
echo "Usage: $0 [setup|cleanup]"
exit 1