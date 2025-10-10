#!/bin/bash

# SCENARIO: Default Deny Ingress
# SYMPTOM: Client cannot connect to server, connection times out
# THE FIX: Add NetworkPolicy rule to allow traffic from client pod or remove the deny-all policy


NAMESPACE="test-scenario-15"

if [ "$1" = "setup" ]; then
    echo "Setting up scenario 15..."
    
    kubectl --context kind-kubently create namespace $NAMESPACE --dry-run=client -o yaml | kubectl --context kind-kubently apply -f -
    cat <<EOF | kubectl --context kind-kubently apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: server
  namespace: $NAMESPACE
spec:
  replicas: 1
  selector:
    matchLabels:
      app: server
  template:
    metadata:
      labels:
    app: server
    spec:
      containers:
      - name: nginx
    image: nginx:latest
    ports:
    - containerPort: 80
---
apiVersion: v1
kind: Service
metadata:
  name: server-service
  namespace: $NAMESPACE
spec:
  selector:
    app: server
  ports:
  - port: 80
    targetPort: 80
---
apiVersion: v1
kind: Pod
metadata:
  name: client
  namespace: $NAMESPACE
  labels:
    app: client
spec:
  containers:
  - name: client
    image: busybox:latest
    command: ["sh", "-c", "while true; do echo 'Trying to connect...'; wget -O- -T 5 http://server-service 2>&1 | head -5; echo '---'; sleep 5; done"]
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-all-ingress
  namespace: $NAMESPACE
spec:
  podSelector:
    matchLabels:
      app: server
  policyTypes:
  - Ingress
EOF
    kubectl --context kind-kubently get networkpolicy -n $NAMESPACE
    kubectl --context kind-kubently logs client -n $NAMESPACE --tail=10
    
    # Wait for resources to be ready
    sleep 5
    
    exit 0
fi

if [ "$1" = "cleanup" ]; then
    echo "Cleaning up scenario 15..."
    kubectl --context kind-kubently delete namespace $NAMESPACE --ignore-not-found=true
    exit 0
fi

# If no argument provided, show usage
echo "Usage: $0 [setup|cleanup]"
exit 1