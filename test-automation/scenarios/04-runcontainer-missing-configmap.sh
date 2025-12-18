#!/bin/bash

# SCENARIO: RunContainerError (Missing ConfigMap)
# SYMPTOM: Pod stuck in CreateContainerConfigError state
# THE FIX: Create the missing ConfigMap 'app-config' or remove the envFrom reference

# Allow context override: KUBE_CONTEXT=kind-kind-exec-only ./04-runcontainer-missing-configmap.sh setup
KUBE_CONTEXT="${KUBE_CONTEXT:-kind-kubently}"
NAMESPACE="test-scenario-04"

if [ "$1" = "setup" ]; then
    echo "Setting up scenario 04 on context: $KUBE_CONTEXT..."

    kubectl --context $KUBE_CONTEXT create namespace $NAMESPACE --dry-run=client -o yaml | kubectl --context $KUBE_CONTEXT apply -f -
    cat <<EOF | kubectl --context $KUBE_CONTEXT apply -f -
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: payment-service
      namespace: $NAMESPACE
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: payment
      template:
        metadata:
          labels:
            app: payment
        spec:
          containers:
          - name: api
            image: busybox:latest
            command: ["sh", "-c", "env && sleep 3600"]
            envFrom:
            - configMapRef:
                name: app-config
EOF
    
    # Wait for resources to be ready
    sleep 5
    
    exit 0
fi

if [ "$1" = "cleanup" ]; then
    echo "Cleaning up scenario 04 on context: $KUBE_CONTEXT..."
    kubectl --context $KUBE_CONTEXT delete namespace $NAMESPACE --ignore-not-found=true
    exit 0
fi

# If no argument provided, show usage
echo "Usage: $0 [setup|cleanup]"
exit 1