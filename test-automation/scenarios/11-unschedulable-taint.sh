#!/bin/bash

# SCENARIO: Unschedulable (Taint/Toleration)
# SYMPTOM: Pod stuck in Pending state with "node(s) had taint" event
# THE FIX: Add toleration for 'app=critical:NoSchedule' taint or remove taint from nodes


NAMESPACE="test-scenario-11"

if [ "$1" = "setup" ]; then
    echo "Setting up scenario 11..."

    kubectl --context kind-kubently create namespace $NAMESPACE --dry-run=client -o yaml | kubectl --context kind-kubently apply -f -
    # Taint all nodes
    kubectl --context kind-kubently taint nodes --all app=critical:NoSchedule --overwrite
    cat <<EOF | kubectl --context kind-kubently apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: no-toleration-deployment
  namespace: $NAMESPACE
spec:
  replicas: 1
  selector:
    matchLabels:
      app: intolerant-app
  template:
    metadata:
      labels:
        app: intolerant-app
    spec:
      containers:
      - name: app
        image: busybox:latest
        command: ["sh", "-c", "sleep 3600"]
EOF
    kubectl --context kind-kubently get pods -n $NAMESPACE
    kubectl --context kind-kubently describe pod -n $NAMESPACE | grep -A 5 Events
    
    # Wait for resources to be ready
    sleep 5
    
    exit 0
fi

if [ "$1" = "cleanup" ]; then
    echo "Cleaning up scenario 11..."
    kubectl --context kind-kubently delete namespace $NAMESPACE --ignore-not-found=true
    # Remove the taint from all nodes
    kubectl --context kind-kubently taint nodes --all app=critical:NoSchedule- 2>/dev/null || true
    exit 0
fi

# If no argument provided, show usage
echo "Usage: $0 [setup|cleanup]"
exit 1