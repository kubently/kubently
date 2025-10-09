#!/bin/bash

# SCENARIO: RunContainerError (Missing Secret Key)
# SYMPTOM: Pod stuck in CreateContainerConfigError state
# THE FIX: Add the missing 'api-key' key to the secret or use a key that exists in the secret


NAMESPACE="test-scenario-05"

if [ "$1" = "setup" ]; then
    echo "Setting up scenario 05..."
    
    kubectl --context kind-kubently create namespace $NAMESPACE --dry-run=client -o yaml | kubectl --context kind-kubently apply -f -
    cat <<EOF | kubectl --context kind-kubently apply -f -
    apiVersion: v1
    kind: Secret
    metadata:
      name: app-secret
      namespace: $NAMESPACE
    type: Opaque
    data:
      database-password: cGFzc3dvcmQxMjM=
EOF
    cat <<EOF | kubectl --context kind-kubently apply -f -
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: missing-secret-key-deployment
      namespace: $NAMESPACE
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: secret-app
      template:
        metadata:
          labels:
            app: secret-app
        spec:
          containers:
          - name: app
            image: busybox:latest
            command: ["sh", "-c", "echo API_KEY=\$API_KEY && sleep 3600"]
            env:
            - name: API_KEY
              valueFrom:
                secretKeyRef:
                  name: app-secret
                  key: api-key
EOF
    
    # Wait for resources to be ready
    sleep 5
    
    exit 0
fi

if [ "$1" = "cleanup" ]; then
    echo "Cleaning up scenario 05..."
    kubectl --context kind-kubently delete namespace $NAMESPACE --ignore-not-found=true
    exit 0
fi

# If no argument provided, show usage
echo "Usage: $0 [setup|cleanup]"
exit 1