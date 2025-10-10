#!/bin/bash

# SCENARIO: RBAC Forbidden (ClusterRole)
# SYMPTOM: 403 Forbidden when trying to access resources in different namespace
# THE FIX: Use ClusterRoleBinding instead of RoleBinding for cross-namespace access


NAMESPACE="test-scenario-20"

if [ "$1" = "setup" ]; then
    echo "Setting up scenario 20..."
    
    kubectl --context kind-kubently create namespace $NAMESPACE-a --dry-run=client -o yaml | kubectl --context kind-kubently apply -f -
    kubectl --context kind-kubently create namespace $NAMESPACE-b --dry-run=client -o yaml | kubectl --context kind-kubently apply -f -
    cat <<EOF | kubectl --context kind-kubently apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: cross-namespace-sa
  namespace: $NAMESPACE-a
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: pod-reader
rules:
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: pod-reader-binding
  namespace: $NAMESPACE-a
subjects:
- kind: ServiceAccount
  name: cross-namespace-sa
  namespace: $NAMESPACE-a
roleRef:
  kind: ClusterRole
  name: pod-reader
  apiGroup: rbac.authorization.k8s.io
---
apiVersion: v1
kind: Pod
metadata:
  name: test-pod-b
  namespace: $NAMESPACE-b
spec:
  containers:
  - name: nginx
    image: nginx:latest
---
apiVersion: v1
kind: Pod
metadata:
  name: rbac-test-pod
  namespace: $NAMESPACE-a
spec:
  serviceAccountName: cross-namespace-sa
  containers:
  - name: kubectl
    image: bitnami/kubectl:latest
    command: ["sh", "-c", "
      echo 'Testing cross-namespace RBAC...';
      echo '';
      echo 'Listing pods in same namespace (test-scenario-20-a) - should work:';
      kubectl --context kind-kubently get pods -n test-scenario-20-a;
      echo '';
      echo 'Listing pods in different namespace (test-scenario-20-b) - will fail with 403:';
      kubectl --context kind-kubently get pods -n test-scenario-20-b 2>&1;
      echo '';
      echo 'The fix: Use ClusterRoleBinding instead of RoleBinding';
      sleep 3600
    "]
EOF
    kubectl --context kind-kubently logs rbac-test-pod -n $NAMESPACE-a
    
    # Wait for resources to be ready
    sleep 5
    
    exit 0
fi

if [ "$1" = "cleanup" ]; then
    echo "Cleaning up scenario 20..."
    kubectl --context kind-kubently delete namespace $NAMESPACE --ignore-not-found=true
    exit 0
fi

# If no argument provided, show usage
echo "Usage: $0 [setup|cleanup]"
exit 1