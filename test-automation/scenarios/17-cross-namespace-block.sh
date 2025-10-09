#!/bin/bash

# SCENARIO: Cross-Namespace Block
# SYMPTOM: Pod in namespace-a cannot connect to service in namespace-b
# THE FIX: Update NetworkPolicy to allow ingress from namespace-a or remove the policy


NAMESPACE="test-scenario-17"

if [ "$1" = "setup" ]; then
    echo "Setting up scenario 17..."
    
    # Note: This scenario uses namespace-a and namespace-b for cross-namespace testing
    kubectl --context kind-kubently create namespace namespace-a --dry-run=client -o yaml | kubectl --context kind-kubently apply -f -
    kubectl --context kind-kubently create namespace namespace-b --dry-run=client -o yaml | kubectl --context kind-kubently apply -f -
    cat <<EOF | kubectl --context kind-kubently apply -f -
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: server-b
      namespace: namespace-b
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: server-b
      template:
        metadata:
          labels:
            app: server-b
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
      name: server-b-service
      namespace: namespace-b
    spec:
      selector:
        app: server-b
      ports:
      - port: 80
        targetPort: 80
EOF
    cat <<EOF | kubectl --context kind-kubently apply -f -
    apiVersion: v1
    kind: Pod
    metadata:
      name: client-a
      namespace: namespace-a
      labels:
        app: client-a
    spec:
      containers:
      - name: client
        image: busybox:latest
        command: ["sh", "-c", "while true; do echo 'Trying cross-namespace connection...'; wget -O- -T 5 http://server-b-service.namespace-b.svc.cluster.local 2>&1 | head -5; echo '---'; sleep 5; done"]
EOF
    cat <<EOF | kubectl --context kind-kubently apply -f -
    apiVersion: networking.k8s.io/v1
    kind: NetworkPolicy
    metadata:
      name: same-namespace-only
      namespace: namespace-b
    spec:
      podSelector:
        matchLabels:
          app: server-b
      policyTypes:
      - Ingress
      ingress:
      - from:
        - namespaceSelector:
            matchLabels:
              name: namespace-b
EOF
    kubectl --context kind-kubently get networkpolicy -n namespace-b
    kubectl --context kind-kubently logs client-a -n namespace-a --tail=10
    
    # Wait for resources to be ready
    sleep 5
    
    exit 0
fi

if [ "$1" = "cleanup" ]; then
    echo "Cleaning up scenario 17..."
    kubectl --context kind-kubently delete namespace namespace-a --ignore-not-found=true
    kubectl --context kind-kubently delete namespace namespace-b --ignore-not-found=true
    exit 0
fi

# If no argument provided, show usage
echo "Usage: $0 [setup|cleanup]"
exit 1