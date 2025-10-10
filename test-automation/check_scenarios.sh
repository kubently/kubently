#!/bin/bash

# Script to quickly test all failed scenarios for setup issues

SCENARIOS=(
  "05-runcontainer-missing-secret-key.sh"
  "09-mismatched-labels.sh"
  "10-unschedulable-resources.sh"
  "11-unschedulable-taint.sh"
  "15-network-policy-deny-ingress.sh"
  "16-network-policy-deny-egress.sh"
  "17-cross-namespace-block.sh"
  "18-missing-serviceaccount.sh"
  "19-rbac-forbidden-role.sh"
  "20-rbac-forbidden-clusterrole.sh"
)

for scenario in "${SCENARIOS[@]}"; do
  echo "======================================"
  echo "Testing: $scenario"
  echo "======================================"

  # Cleanup first
  bash scenarios/$scenario cleanup 2>/dev/null

  # Setup and capture output
  setup_output=$(bash scenarios/$scenario setup 2>&1)
  setup_exit_code=$?

  if [ $setup_exit_code -ne 0 ]; then
    echo "SETUP FAILED with exit code $setup_exit_code"
    echo "$setup_output" | head -20
  else
    echo "Setup successful"
    # Extract namespace from script
    namespace=$(grep '^NAMESPACE=' scenarios/$scenario | cut -d'=' -f2 | tr -d '"')
    if [ -n "$namespace" ]; then
      echo "Checking resources in namespace $namespace:"
      kubectl get all -n $namespace 2>/dev/null | head -10
    fi
  fi

  echo ""
done

echo "======================================"
echo "Cleanup all test namespaces"
echo "======================================"
for scenario in "${SCENARIOS[@]}"; do
  bash scenarios/$scenario cleanup 2>/dev/null
done
