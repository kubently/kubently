#!/bin/bash

# Test script to demonstrate Gemini integration
# Usage: ./test_gemini.sh

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Kubently Test Automation - Gemini Demo${NC}"
echo -e "${BLUE}========================================${NC}\n"

# Check for Google API key
if [ -z "$GOOGLE_API_KEY" ]; then
    echo -e "${YELLOW}GOOGLE_API_KEY not set.${NC}"
    echo -e "${YELLOW}Gemini analysis provides:${NC}"
    echo "  • AI-powered root cause analysis"
    echo "  • Intelligent bottleneck detection"
    echo "  • Specific improvement recommendations"
    echo "  • Quality scoring with explanations"
    echo ""
    echo -e "${YELLOW}To enable Gemini:${NC}"
    echo "  1. Get API key from: https://makersuite.google.com/app/apikey"
    echo "  2. export GOOGLE_API_KEY='your-key-here'"
    echo ""
    echo -e "${RED}Continuing with basic heuristic analysis only...${NC}\n"
else
    echo -e "${GREEN}✓ Gemini API key found${NC}\n"
fi

# Check for Kubently API key
if [ -z "$KUBENTLY_API_KEY" ]; then
    echo -e "${YELLOW}Enter Kubently API key:${NC}"
    read -s api_key
    export KUBENTLY_API_KEY="$api_key"
fi

# Install dependencies if needed
if ! python3 -c "import google.generativeai" 2>/dev/null; then
    echo -e "${YELLOW}Installing google-generativeai...${NC}"
    pip install google-generativeai
fi

# Option 1: Run new test with Gemini analysis
echo -e "\n${BLUE}Option 1: Run new test with automatic Gemini analysis${NC}"
echo "Command: python3 comprehensive_test_runner.py --scenario 01-imagepullbackoff-typo --use-gemini"

# Option 2: Analyze previous result
echo -e "\n${BLUE}Option 2: Analyze previous test result with Gemini${NC}"
echo "Command: python3 comprehensive_test_runner.py --analyze-previous <result-file.json>"

# Option 3: Use standalone analyzer
echo -e "\n${BLUE}Option 3: Use standalone Gemini analyzer${NC}"
echo "Commands:"
echo "  python3 gemini_analyzer.py --latest           # Analyze most recent test"
echo "  python3 gemini_analyzer.py --scenario 01      # Analyze latest for scenario"
echo "  python3 gemini_analyzer.py --all              # Analyze all results"

# Demo: Find and analyze latest result
echo -e "\n${GREEN}Demo: Analyzing most recent test result...${NC}"

LATEST=$(ls -t comprehensive-results/*.json 2>/dev/null | head -1)

if [ -n "$LATEST" ]; then
    echo -e "Found: $(basename $LATEST)\n"
    
    if [ -n "$GOOGLE_API_KEY" ]; then
        echo -e "${BLUE}Running Gemini analysis...${NC}"
        python3 gemini_analyzer.py --result-file "$LATEST" --output /tmp/gemini_analysis.json
        
        # Show summary
        echo -e "\n${GREEN}Analysis Summary:${NC}"
        python3 -c "
import json
with open('/tmp/gemini_analysis.json') as f:
    data = json.load(f)
    
    if 'quality_assessment' in data:
        qa = data['quality_assessment']
        print(f\"Overall Quality: {qa.get('overall_score', 'N/A')}/10\")
        print(f\"Accuracy: {qa.get('accuracy', 'N/A')}/10\")
        print(f\"Explanation: {qa.get('explanation', 'N/A')}\")
    
    if 'bottlenecks' in data and data['bottlenecks']:
        print(f\"\nTop Bottlenecks:\")
        for b in data['bottlenecks'][:3]:
            if isinstance(b, dict):
                print(f\"  • {b.get('description', b)}\")
            else:
                print(f\"  • {b}\")
    
    if 'recommendations' in data and data['recommendations']:
        print(f\"\nTop Recommendations:\")
        for r in data['recommendations'][:3]:
            print(f\"  • {r}\")
        "
    else:
        echo -e "${YELLOW}Running basic heuristic analysis...${NC}"
        python3 -c "
import json
with open('$LATEST') as f:
    data = json.load(f)
    
    # Basic analysis
    trace = data.get('debug_trace', {})
    scenario = data.get('scenario', {})
    
    print(f\"Scenario: {scenario.get('name', 'Unknown')}\")
    print(f\"Duration: {trace.get('duration_seconds', 0):.2f}s\")
    print(f\"Tool Calls: {len(trace.get('tool_calls', []))}\")
    
    responses = ' '.join(trace.get('responses', [])).lower()
    expected = scenario.get('expected_fix', '').lower()
    
    if any(kw in responses for kw in expected.split() if len(kw) > 3):
        print('Root Cause: ✓ Found')
    else:
        print('Root Cause: ✗ Not found')
        "
    fi
else
    echo -e "${YELLOW}No test results found. Run a test first:${NC}"
    echo "  ./run_tests.sh comprehensive 01-imagepullbackoff-typo"
fi

echo -e "\n${BLUE}========================================${NC}"