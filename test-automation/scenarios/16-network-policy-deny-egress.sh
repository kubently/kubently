#!/bin/bash

# SCENARIO: Default Deny Egress
# SYMPTOM: Pod cannot connect to external internet (8.8.8.8)
# THE FIX: Add egress rule to allow external traffic or remove the egress NetworkPolicy


NAMESPACE="test-scenario-16"

if [ "$1" = "setup" ]; then
    echo "Setting up scenario 16..."
    
    kubectl --context kind-kubently create namespace $NAMESPACE --dry-run=client -o yaml | kubectl --context kind-kubently apply -f -
    cat <<EOF | kubectl --context kind-kubently apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: internet-client
  namespace: $NAMESPACE
  labels:
    app: internet-client
spec:
  containers:
  - name: client
    image: busybox:latest
    command: ["sh", "-c", "while true; do echo 'Trying to reach 8.8.8.8...'; ping -c 3 -W 2 8.8.8.8 2>&1; echo '---'; sleep 5; done"]
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-external-egress
  namespace: $NAMESPACE
spec:
  podSelector:
    matchLabels:
      app: internet-client
  policyTypes:
  - Egress
  egress:
  - to:
    - namespaceSelector: {}
    ports:
    - protocol: TCP
      port: 53
    - protocol: UDP
      port: 53
  - to:
    - podSelector: {}
EOF
    kubectl --context kind-kubently get networkpolicy -n $NAMESPACE
    kubectl --context kind-kubently logs internet-client -n $NAMESPACE --tail=15
    
    # Wait for resources to be ready
    sleep 5
    
    exit 0
fi

if [ "$1" = "cleanup" ]; then
    echo "Cleaning up scenario 16..."
    kubectl --context kind-kubently delete namespace $NAMESPACE --ignore-not-found=true
    exit 0
fi

# If no argument provided, show usage
echo "Usage: $0 [setup|cleanup]"
exit 1