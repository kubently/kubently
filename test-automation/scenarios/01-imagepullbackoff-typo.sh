#!/bin/bash

# SCENARIO: ImagePullBackOff (Typo)
# SYMPTOM: Pod stuck in ImagePullBackOff state
# Expected fix: Correct the image name from busyboxx to busybox

NAMESPACE="test-scenario-01"

if [ "$1" = "setup" ]; then
    echo "Setting up scenario 01..."
    
    kubectl --context kind-kubently create namespace $NAMESPACE --dry-run=client -o yaml | kubectl --context kind-kubently apply -f -
    
    cat <<EOF | kubectl --context kind-kubently apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: typo-image-deployment
  namespace: $NAMESPACE
spec:
  replicas: 1
  selector:
    matchLabels:
      app: typo-app
  template:
    metadata:
      labels:
        app: typo-app
    spec:
      containers:
      - name: busybox
        image: busyboxx:latest
        command: ["sh", "-c", "sleep 3600"]
EOF
    
    # Wait for resources to be ready
    sleep 5
    
    exit 0
fi

if [ "$1" = "cleanup" ]; then
    echo "Cleaning up scenario 01..."
    kubectl --context kind-kubently delete namespace $NAMESPACE --ignore-not-found=true
    exit 0
fi

# If no argument provided, show usage
echo "Usage: $0 [setup|cleanup]"
exit 1