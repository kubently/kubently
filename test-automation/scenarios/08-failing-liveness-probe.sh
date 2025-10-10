#!/bin/bash

# SCENARIO: Failing Liveness Probe
# SYMPTOM: Pod continuously restarts due to failed liveness probe
# THE FIX: Fix liveness probe path to '/' or adjust probe thresholds


NAMESPACE="test-scenario-08"

if [ "$1" = "setup" ]; then
    echo "Setting up scenario 08..."
    
    kubectl --context kind-kubently create namespace $NAMESPACE --dry-run=client -o yaml | kubectl --context kind-kubently apply -f -
    cat <<EOF | kubectl --context kind-kubently apply -f -
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: liveness-failure-deployment
      namespace: $NAMESPACE
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: liveness-app
      template:
        metadata:
          labels:
            app: liveness-app
        spec:
          containers:
          - name: nginx
            image: nginx:latest
            ports:
            - containerPort: 80
            livenessProbe:
              httpGet:
                path: /healthz
                port: 80
              initialDelaySeconds: 10
              periodSeconds: 5
              failureThreshold: 3
EOF
    
    # Wait for resources to be ready
    sleep 5
    
    exit 0
fi

if [ "$1" = "cleanup" ]; then
    echo "Cleaning up scenario 08..."
    kubectl --context kind-kubently delete namespace $NAMESPACE --ignore-not-found=true
    exit 0
fi

# If no argument provided, show usage
echo "Usage: $0 [setup|cleanup]"
exit 1