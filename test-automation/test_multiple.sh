#!/bin/bash
# Test multiple scenarios

scenarios=(
    "01-imagepullbackoff-typo"
    "03-crashloopbackoff"
    "06-oomkilled"
    "13-service-selector-mismatch"
    "15-network-policy-deny-ingress"
    "17-cross-namespace-block"
)

echo "Testing ${#scenarios[@]} scenarios..."
echo

for scenario in "${scenarios[@]}"; do
    echo "===== Testing $scenario ====="
    ./run_tests.sh --api-key test-api-key --scenario $scenario 2>&1 | grep -E "(Testing Scenario:|Root Cause Found:|Tool Calls:|Duration:|✅|❌)"
    echo
done

echo "Test complete!"