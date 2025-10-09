#!/bin/bash

# Quick test script for Kubently automated testing
# Usage: ./quick_test.sh [scenario_number]

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Check for API key
if [ -z "$KUBENTLY_API_KEY" ]; then
    echo -e "${YELLOW}Enter your Kubently API key:${NC}"
    read -s api_key
    export KUBENTLY_API_KEY="$api_key"
fi

# Default to test scenario 01 if not specified
SCENARIO=${1:-01-imagepullbackoff-typo}

echo -e "${GREEN}Running Kubently test for scenario: $SCENARIO${NC}"

# Run the test
python3 comprehensive_test_runner.py \
    --api-key "$KUBENTLY_API_KEY" \
    --scenario "$SCENARIO" 2>/dev/null

# Check latest results
LATEST_RESULT=$(ls -t comprehensive-results/${SCENARIO}_*.json 2>/dev/null | head -1)

if [ -n "$LATEST_RESULT" ]; then
    echo -e "\n${GREEN}Test Results Summary:${NC}"
    python3 -c "
import json
with open('$LATEST_RESULT') as f:
    data = json.load(f)
    
    # Extract key information
    trace = data.get('debug_trace', {})
    analysis = data.get('analysis', {})
    
    print(f\"Scenario: {data.get('scenario', {}).get('name', 'Unknown')}\")
    print(f\"Duration: {trace.get('duration_seconds', 0):.2f}s\")
    print(f\"Root Cause Found: {'Yes' if analysis.get('root_cause_analysis', {}).get('identified_correctly') else 'No'}\")
    
    if trace.get('responses'):
        response = trace['responses'][0]
        print(f\"\nKubently Response (first 300 chars):\")
        print(response[:300])
        
        # Check if correct fix was identified
        expected = data.get('scenario', {}).get('expected_fix', '').lower()
        if 'busybox' in response.lower() and 'busyboxx' in response.lower():
            print(f\"\n✓ Correctly identified the typo issue!\")
    "
    
    echo -e "\n${GREEN}Full results saved to: $LATEST_RESULT${NC}"
else
    echo -e "${RED}No results found${NC}"
fi