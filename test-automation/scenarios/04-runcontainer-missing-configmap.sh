#!/bin/bash

# SCENARIO: RunContainerError (Missing ConfigMap)
# SYMPTOM: Pod stuck in CreateContainerConfigError state
# THE FIX: Create the missing ConfigMap 'app-config' or remove the envFrom reference


NAMESPACE="test-scenario-04"

if [ "$1" = "setup" ]; then
    echo "Setting up scenario 04..."
    
    kubectl --context kind-kubently create namespace $NAMESPACE --dry-run=client -o yaml | kubectl --context kind-kubently apply -f -
    cat <<EOF | kubectl --context kind-kubently apply -f -
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: missing-configmap-deployment
      namespace: $NAMESPACE
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: configmap-app
      template:
        metadata:
          labels:
            app: configmap-app
        spec:
          containers:
          - name: app
            image: busybox:latest
            command: ["sh", "-c", "env && sleep 3600"]
            envFrom:
            - configMapRef:
                name: app-config
EOF
    
    # Wait for resources to be ready
    sleep 5
    
    exit 0
fi

if [ "$1" = "cleanup" ]; then
    echo "Cleaning up scenario 04..."
    kubectl --context kind-kubently delete namespace $NAMESPACE --ignore-not-found=true
    exit 0
fi

# If no argument provided, show usage
echo "Usage: $0 [setup|cleanup]"
exit 1