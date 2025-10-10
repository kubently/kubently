#!/bin/bash

# SCENARIO: Unschedulable (Insufficient Resources)
# SYMPTOM: Pod stuck in Pending state with "Insufficient cpu" or "Insufficient memory" event
# THE FIX: Reduce resource requests to reasonable values (e.g., cpu: "1", memory: "1Gi")


NAMESPACE="test-scenario-10"

if [ "$1" = "setup" ]; then
    echo "Setting up scenario 10..."
    
    kubectl --context kind-kubently create namespace $NAMESPACE --dry-run=client -o yaml | kubectl --context kind-kubently apply -f -
    cat <<EOF | kubectl --context kind-kubently apply -f -
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: high-resources-deployment
      namespace: $NAMESPACE
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: resource-hog
      template:
        metadata:
          labels:
            app: resource-hog
        spec:
          containers:
          - name: resource-hog
            image: busybox:latest
            command: ["sh", "-c", "sleep 3600"]
            resources:
              requests:
                cpu: "1000"
                memory: "500Gi"
              limits:
                cpu: "1000"
                memory: "500Gi"
EOF
    kubectl --context kind-kubently get pods -n $NAMESPACE
    kubectl --context kind-kubently describe pod -n $NAMESPACE | grep -A 5 Events
    
    # Wait for resources to be ready
    sleep 5
    
    exit 0
fi

if [ "$1" = "cleanup" ]; then
    echo "Cleaning up scenario 10..."
    kubectl --context kind-kubently delete namespace $NAMESPACE --ignore-not-found=true
    exit 0
fi

# If no argument provided, show usage
echo "Usage: $0 [setup|cleanup]"
exit 1