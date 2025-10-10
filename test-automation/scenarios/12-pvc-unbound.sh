#!/bin/bash

# SCENARIO: PVC Unbound (Bad StorageClass)
# SYMPTOM: PVC stuck in Pending, Pod stuck in Pending with "pod has unbound immediate PersistentVolumeClaims"
# THE FIX: Use an existing StorageClass or create the missing 'fast-ssd' StorageClass


NAMESPACE="test-scenario-12"

if [ "$1" = "setup" ]; then
    echo "Setting up scenario 12..."
    
    kubectl --context kind-kubently create namespace $NAMESPACE --dry-run=client -o yaml | kubectl --context kind-kubently apply -f -
    cat <<EOF | kubectl --context kind-kubently apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: unbound-pvc
  namespace: $NAMESPACE
spec:
  accessModes:
  - ReadWriteOnce
  storageClassName: fast-ssd
  resources:
    requests:
      storage: 1Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pvc-deployment
  namespace: $NAMESPACE
spec:
  replicas: 1
  selector:
    matchLabels:
      app: storage-app
  template:
    metadata:
      labels:
    app: storage-app
    spec:
      containers:
      - name: app
    image: busybox:latest
    command: ["sh", "-c", "sleep 3600"]
    volumeMounts:
    - name: storage
      mountPath: /data
      volumes:
      - name: storage
    persistentVolumeClaim:
      claimName: unbound-pvc
EOF
    kubectl --context kind-kubently get pvc -n $NAMESPACE
    kubectl --context kind-kubently get pods -n $NAMESPACE
    kubectl --context kind-kubently describe pod -n $NAMESPACE | grep -A 5 Events
    
    # Wait for resources to be ready
    sleep 5
    
    exit 0
fi

if [ "$1" = "cleanup" ]; then
    echo "Cleaning up scenario 12..."
    kubectl --context kind-kubently delete namespace $NAMESPACE --ignore-not-found=true
    exit 0
fi

# If no argument provided, show usage
echo "Usage: $0 [setup|cleanup]"
exit 1