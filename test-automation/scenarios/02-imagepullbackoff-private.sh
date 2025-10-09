#!/bin/bash

# SCENARIO: ImagePullBackOff (Private Registry)
# SYMPTOM: Pod stuck in ImagePullBackOff or ErrImagePull state with "pull access denied" error
# THE FIX: Create and reference an imagePullSecret with valid registry credentials


NAMESPACE="test-scenario-02"

if [ "$1" = "setup" ]; then
    echo "Setting up scenario 02..."
    
    kubectl --context kind-kubently create namespace $NAMESPACE --dry-run=client -o yaml | kubectl --context kind-kubently apply -f -
    cat <<EOF | kubectl --context kind-kubently apply -f -
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: private-registry-deployment
      namespace: $NAMESPACE
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: private-app
      template:
        metadata:
          labels:
            app: private-app
        spec:
          containers:
          - name: private-image
            image: gcr.io/private-project/private-app:latest
            command: ["sh", "-c", "sleep 3600"]
EOF
    
    # Wait for resources to be ready
    sleep 5
    
    exit 0
fi

if [ "$1" = "cleanup" ]; then
    echo "Cleaning up scenario 02..."
    kubectl --context kind-kubently delete namespace $NAMESPACE --ignore-not-found=true
    exit 0
fi

# If no argument provided, show usage
echo "Usage: $0 [setup|cleanup]"
exit 1