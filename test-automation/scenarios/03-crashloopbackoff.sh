#!/bin/bash

# SCENARIO: CrashLoopBackOff
# SYMPTOM: Pod repeatedly crashes and restarts, showing CrashLoopBackOff status
# THE FIX: Fix the command to not exit with error code (remove 'exit 1' or change to successful command)


NAMESPACE="test-scenario-03"

if [ "$1" = "setup" ]; then
    echo "Setting up scenario 03..."
    
    kubectl --context kind-kubently create namespace $NAMESPACE --dry-run=client -o yaml | kubectl --context kind-kubently apply -f -
    cat <<EOF | kubectl --context kind-kubently apply -f -
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: crash-loop-deployment
      namespace: $NAMESPACE
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: crash-app
      template:
        metadata:
          labels:
            app: crash-app
        spec:
          containers:
          - name: failing-container
            image: busybox:latest
            command: ["sh", "-c", "echo 'I am failing' && exit 1"]
EOF
    
    # Wait for resources to be ready
    sleep 5
    
    exit 0
fi

if [ "$1" = "cleanup" ]; then
    echo "Cleaning up scenario 03..."
    kubectl --context kind-kubently delete namespace $NAMESPACE --ignore-not-found=true
    exit 0
fi

# If no argument provided, show usage
echo "Usage: $0 [setup|cleanup]"
exit 1