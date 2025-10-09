#!/bin/bash

# SCENARIO: RBAC Forbidden (Role)
# SYMPTOM: kubectl --context kind-kubently commands return 403 Forbidden when trying to list or delete pods
# THE FIX: Update Role to include 'list' and 'delete' verbs, not just 'get'


NAMESPACE="test-scenario-19"

if [ "$1" = "setup" ]; then
    echo "Setting up scenario 19..."
    
    kubectl --context kind-kubently create namespace $NAMESPACE --dry-run=client -o yaml | kubectl --context kind-kubently apply -f -
    cat <<EOF | kubectl --context kind-kubently apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: limited-sa
  namespace: $NAMESPACE
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: limited-role
  namespace: $NAMESPACE
rules:
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: limited-binding
  namespace: $NAMESPACE
subjects:
- kind: ServiceAccount
  name: limited-sa
  namespace: $NAMESPACE
roleRef:
  kind: Role
  name: limited-role
  apiGroup: rbac.authorization.k8s.io
---
apiVersion: v1
kind: Pod
metadata:
  name: rbac-test-pod
  namespace: $NAMESPACE
spec:
  serviceAccountName: limited-sa
  containers:
  - name: kubectl
    image: bitnami/kubectl:latest
    command: ["sh", "-c", "
      echo 'Testing RBAC permissions...';
      echo '';
      echo 'Trying to GET a single pod (should work):';
      kubectl --context kind-kubently get pod rbac-test-pod -n test-scenario-19;
      echo '';
      echo 'Trying to LIST pods (will fail with 403):';
      kubectl --context kind-kubently get pods -n test-scenario-19 2>&1;
      echo '';
      echo 'Trying to DELETE a pod (will fail with 403):';
      kubectl --context kind-kubently delete pod test-pod -n test-scenario-19 --dry-run=client 2>&1;
      echo '';
      sleep 3600
    "]
EOF
    kubectl --context kind-kubently logs rbac-test-pod -n $NAMESPACE
    
    # Wait for resources to be ready
    sleep 5
    
    exit 0
fi

if [ "$1" = "cleanup" ]; then
    echo "Cleaning up scenario 19..."
    kubectl --context kind-kubently delete namespace $NAMESPACE --ignore-not-found=true
    exit 0
fi

# If no argument provided, show usage
echo "Usage: $0 [setup|cleanup]"
exit 1