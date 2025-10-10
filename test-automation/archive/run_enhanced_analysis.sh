#!/bin/bash
# Run enhanced test automation with full analysis

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Kubently Enhanced Test Automation ===${NC}"
echo ""

# Check for required environment variables
if [ -z "$GOOGLE_API_KEY" ]; then
    echo -e "${YELLOW}Warning: GOOGLE_API_KEY not set. Will use heuristic analysis only.${NC}"
    echo "To enable Gemini analysis, run: export GOOGLE_API_KEY=your_key"
    echo ""
fi

# Get API key for Kubently
API_KEY="${KUBENTLY_API_KEY:-test-key}"
echo -e "${GREEN}Using API Key:${NC} $API_KEY"

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install requirements if needed
echo -e "${GREEN}Installing/updating dependencies...${NC}"
pip install -q --upgrade pip
pip install -q httpx rich pyyaml python-dotenv google-generativeai

# Create results directories
mkdir -p comprehensive-results-enhanced
mkdir -p comprehensive-results-enhanced/traces
mkdir -p comprehensive-results-enhanced/analysis

# Check if Kubently is running
echo -e "${GREEN}Checking Kubently service...${NC}"
if ! curl -s http://localhost:8080/health > /dev/null 2>&1; then
    echo -e "${RED}Error: Kubently is not running on localhost:8080${NC}"
    echo "Please ensure Kubently is deployed and port-forwarded"
    exit 1
fi

echo -e "${GREEN}Kubently service is healthy${NC}"
echo ""

# Run specific scenario or all
if [ -n "$1" ]; then
    echo -e "${GREEN}Running scenario: $1${NC}"
    python comprehensive_test_runner_enhanced.py \
        --api-url http://localhost:8080 \
        --api-key "$API_KEY" \
        --scenario "$1" \
        --use-gemini
else
    echo -e "${GREEN}Running all test scenarios...${NC}"
    python comprehensive_test_runner_enhanced.py \
        --api-url http://localhost:8080 \
        --api-key "$API_KEY" \
        --use-gemini
fi

echo ""
echo -e "${GREEN}Test execution complete!${NC}"
echo ""

# Run enhanced analysis if tests completed
if [ -d "comprehensive-results-enhanced" ] && [ "$(ls -A comprehensive-results-enhanced/*.json 2>/dev/null)" ]; then
    echo -e "${GREEN}Running enhanced Gemini analysis...${NC}"
    
    python enhanced_gemini_analyzer.py \
        --result-dir comprehensive-results-enhanced \
        --output comprehensive-results-enhanced/analysis/actionable_report.md
    
    echo ""
    echo -e "${GREEN}=== Analysis Complete ===${NC}"
    echo ""
    echo "Reports generated:"
    echo "  - Actionable report: comprehensive-results-enhanced/analysis/actionable_report.*.md"
    echo "  - JSON report: comprehensive-results-enhanced/analysis/actionable_report.*.json"
    echo ""
    echo -e "${YELLOW}Key Actions:${NC}"
    
    # Extract and display top recommendations
    if [ -f comprehensive-results-enhanced/analysis/actionable_report.*.md ]; then
        grep -A 3 "^#### CRITICAL:" comprehensive-results-enhanced/analysis/actionable_report.*.md 2>/dev/null || true
    fi
else
    echo -e "${RED}No test results found to analyze${NC}"
fi

echo ""
echo -e "${GREEN}Done!${NC}"