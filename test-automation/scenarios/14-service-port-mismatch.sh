#!/bin/bash

# SCENARIO: Service Port Mismatch
# SYMPTOM: Connection refused when accessing service (port mismatch)
# THE FIX: Change Service targetPort from 8080 to 80 to match container port


NAMESPACE="test-scenario-14"

if [ "$1" = "setup" ]; then
    echo "Setting up scenario 14..."
    
    kubectl --context kind-kubently create namespace $NAMESPACE --dry-run=client -o yaml | kubectl --context kind-kubently apply -f -
    cat <<EOF | kubectl --context kind-kubently apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web-deployment
  namespace: $NAMESPACE
spec:
  replicas: 1
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
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
  name: web-service
  namespace: $NAMESPACE
spec:
  selector:
    app: web
  ports:
  - port: 80
    targetPort: 8080
  type: ClusterIP
---
apiVersion: v1
kind: Pod
metadata:
  name: test-client
  namespace: $NAMESPACE
spec:
  containers:
  - name: client
    image: busybox:latest
    command: ["sh", "-c", "while true; do wget -O- http://web-service 2>&1 | head -5; echo '---'; sleep 5; done"]
EOF
    kubectl --context kind-kubently get svc,endpoints -n $NAMESPACE
    kubectl --context kind-kubently logs test-client -n $NAMESPACE --tail=10
    
    # Wait for resources to be ready
    sleep 5
    
    exit 0
fi

if [ "$1" = "cleanup" ]; then
    echo "Cleaning up scenario 14..."
    kubectl --context kind-kubently delete namespace $NAMESPACE --ignore-not-found=true
    exit 0
fi

# If no argument provided, show usage
echo "Usage: $0 [setup|cleanup]"
exit 1