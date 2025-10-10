#!/bin/bash

# SCENARIO: OOMKilled
# SYMPTOM: Pod gets OOMKilled and restarts repeatedly
# THE FIX: Increase memory limit to accommodate actual memory usage (e.g., to 256Mi)


NAMESPACE="test-scenario-06"

if [ "$1" = "setup" ]; then
    echo "Setting up scenario 06..."
    
    kubectl --context kind-kubently create namespace $NAMESPACE --dry-run=client -o yaml | kubectl --context kind-kubently apply -f -
    cat <<EOF | kubectl --context kind-kubently apply -f -
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: oom-deployment
      namespace: $NAMESPACE
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: oom-app
      template:
        metadata:
          labels:
            app: oom-app
        spec:
          containers:
          - name: memory-hog
            image: busybox:latest
            command: ["sh", "-c", "dd if=/dev/zero of=/dev/shm/fill bs=1M count=100 && sleep 3600"]
            resources:
              limits:
                memory: "15Mi"
              requests:
                memory: "10Mi"
EOF
    
    # Wait for resources to be ready
    sleep 5
    
    exit 0
fi

if [ "$1" = "cleanup" ]; then
    echo "Cleaning up scenario 06..."
    kubectl --context kind-kubently delete namespace $NAMESPACE --ignore-not-found=true
    exit 0
fi

# If no argument provided, show usage
echo "Usage: $0 [setup|cleanup]"
exit 1