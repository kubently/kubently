#!/bin/bash

# Automated Kubernetes Scenario Testing Script
# Uses Google Gemini for intelligent AI analysis

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=======================================${NC}"
echo -e "${BLUE}Kubently Automated Testing Framework${NC}"
echo -e "${BLUE}=======================================${NC}\n"

# Check environment
echo -e "${YELLOW}Checking environment...${NC}"

# Check for Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Python 3 is required but not installed${NC}"
    exit 1
fi

# Check for kubectl
if ! command -v kubectl &> /dev/null; then
    echo -e "${RED}kubectl is required but not installed${NC}"
    exit 1
fi

# Parse arguments to look for --api-key flag
API_KEY_PROVIDED=""
SCENARIO=""
TEST_TYPE=""
POSITIONAL_ARGS=()

# Collect all arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --api-key)
            API_KEY_PROVIDED="$2"
            shift 2
            ;;
        *)
            POSITIONAL_ARGS+=("$1")
            shift
            ;;
    esac
done

# Now parse positional arguments
if [ ${#POSITIONAL_ARGS[@]} -eq 0 ]; then
    TEST_TYPE="test"
elif [ ${#POSITIONAL_ARGS[@]} -eq 1 ]; then
    if [[ "${POSITIONAL_ARGS[0]}" == "analyze" ]] || [[ "${POSITIONAL_ARGS[0]}" == "test" ]] || [[ "${POSITIONAL_ARGS[0]}" == "recent" ]] || [[ "${POSITIONAL_ARGS[0]}" == "test-and-analyze" ]]; then
        TEST_TYPE="${POSITIONAL_ARGS[0]}"
    else
        # If it's not a command, assume it's a test scenario name
        TEST_TYPE="test"
        SCENARIO="${POSITIONAL_ARGS[0]}"
    fi
elif [ ${#POSITIONAL_ARGS[@]} -ge 2 ]; then
    TEST_TYPE="${POSITIONAL_ARGS[0]}"
    SCENARIO="${POSITIONAL_ARGS[1]}"
fi

# Check for API key - prefer command line, then env, then ask (skip for 'recent' command)
if [ "$TEST_TYPE" != "recent" ]; then
    if [ -n "$API_KEY_PROVIDED" ]; then
        export KUBENTLY_API_KEY="$API_KEY_PROVIDED"
    elif [ -z "$KUBENTLY_API_KEY" ]; then
        echo -e "${YELLOW}KUBENTLY_API_KEY not set. Please provide:${NC}"
        read -p "API Key: " api_key
        export KUBENTLY_API_KEY="$api_key"
    fi
fi

# Check for Gemini API key (optional but recommended)
if [ -z "$GOOGLE_API_KEY" ]; then
    echo -e "${YELLOW}GOOGLE_API_KEY not set. Gemini analysis will be limited.${NC}"
    echo -e "${YELLOW}Set GOOGLE_API_KEY for advanced AI analysis.${NC}"
fi

# Default API URL
API_URL="${KUBENTLY_API_URL:-http://localhost:8080}"

# Install dependencies if needed
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

# Function to select result file for analysis
select_result_file() {
    echo -e "${BLUE}Select a test result to analyze:${NC}\n"
    
    # Check if fzf is available
    if command -v fzf &> /dev/null; then
        # Use fzf for interactive selection with preview
        RESULT_FILE=$(ls -t comprehensive-results/*.json 2>/dev/null | \
            xargs -I {} basename {} | \
            fzf --height=15 --reverse --header="Select result (sorted by most recent)" \
                --preview="echo 'File: {}' && echo '' && jq -r '.scenario.name + \" - \" + .scenario.expected_fix' comprehensive-results/{} 2>/dev/null && echo '' && echo 'Duration: ' && jq -r '.debug_trace.duration_seconds' comprehensive-results/{} 2>/dev/null && echo '' && echo 'Root Cause Found: ' && jq -r '.analysis.root_cause_analysis.identified_correctly' comprehensive-results/{} 2>/dev/null")
        
        if [ -z "$RESULT_FILE" ]; then
            echo -e "${YELLOW}No file selected${NC}"
            exit 0
        fi
    else
        # Fallback to numbered menu
        echo -e "${YELLOW}Tip: Install 'fzf' for a better selection experience${NC}\n"
        
        # List recent results
        results=($(ls -t comprehensive-results/*.json 2>/dev/null | head -20 | xargs -I {} basename {}))
        
        if [ ${#results[@]} -eq 0 ]; then
            echo -e "${RED}No result files found${NC}"
            exit 1
        fi
        
        echo -e "${YELLOW}Recent results (most recent first):${NC}"
        for i in "${!results[@]}"; do
            result_name="${results[$i]}"
            # Parse the filename to show in a friendly way
            scenario=$(echo "$result_name" | cut -d'.' -f1)
            time_part=$(echo "$result_name" | cut -d'.' -f2)
            date_part=$(echo "$result_name" | cut -d'.' -f3)
            
            # Date is already in MM-DD-YYYY format or old YYYYMMDD format
            if [[ $date_part =~ ^([0-9]{4})([0-9]{2})([0-9]{2})$ ]]; then
                # Old format: YYYYMMDD -> MM-DD-YYYY
                formatted_date="${BASH_REMATCH[2]}-${BASH_REMATCH[3]}-${BASH_REMATCH[1]}"
            else
                # New format: already MM-DD-YYYY
                formatted_date=$date_part
            fi
            
            printf "${YELLOW}%2d)${NC} %-35s at %s on %s\n" $((i+1)) "$scenario" "$time_part" "$formatted_date"
        done
        
        echo ""
        read -p "Select result number (1-${#results[@]}): " selection
        
        if [[ "$selection" =~ ^[0-9]+$ ]] && [ "$selection" -ge 1 ] && [ "$selection" -le "${#results[@]}" ]; then
            RESULT_FILE="${results[$((selection-1))]}"
        else
            echo -e "${RED}Invalid selection${NC}"
            exit 1
        fi
    fi
    
    echo -e "${GREEN}Selected: $RESULT_FILE${NC}"
}

# Function to select scenario interactively
select_scenario() {
    echo -e "${BLUE}Available test scenarios:${NC}\n"
    
    # List available scenarios (only numbered test scenarios)
    scenarios=($(ls scenarios/[0-9]*.sh 2>/dev/null | xargs -I {} basename {} .sh | sort))
    
    if [ ${#scenarios[@]} -eq 0 ]; then
        echo -e "${RED}No scenarios found in scenarios/ directory${NC}"
        exit 1
    fi
    
    # Display scenarios with descriptions
    for i in "${!scenarios[@]}"; do
        scenario_name="${scenarios[$i]}"
        # Extract a friendly name from the scenario
        friendly_name=$(echo "$scenario_name" | sed 's/-/ /g' | sed 's/^[0-9]* *//')
        printf "${YELLOW}%2d)${NC} %-30s - %s\n" $((i+1)) "$scenario_name" "$friendly_name"
    done
    
    echo ""
    echo -e "${YELLOW}Options:${NC}"
    echo "  - Enter a number (1-${#scenarios[@]}) to run a specific scenario"
    echo "  - Press Enter to run all scenarios"
    echo ""
    read -p "Your choice: " selection
    
    if [ -z "$selection" ]; then
        echo -e "${GREEN}Running all scenarios...${NC}"
        SCENARIO=""
    elif [[ "$selection" =~ ^[0-9]+$ ]] && [ "$selection" -ge 1 ] && [ "$selection" -le "${#scenarios[@]}" ]; then
        SCENARIO="${scenarios[$((selection-1))]}"
        echo -e "${GREEN}Selected scenario: $SCENARIO${NC}"
    else
        echo -e "${RED}Invalid selection${NC}"
        exit 1
    fi
}

# If test type is "test" and no scenario provided, ask for it
if [ "$TEST_TYPE" == "test" ] && [ -z "$SCENARIO" ]; then
    select_scenario
fi

echo -e "${GREEN}Environment ready!${NC}\n"

# Run tests based on type
case $TEST_TYPE in
    "test")
        echo -e "${BLUE}Running comprehensive tests with full data capture...${NC}"
        python3 comprehensive_test_runner.py \
            --api-url "$API_URL" \
            --api-key "$KUBENTLY_API_KEY" \
            ${SCENARIO:+--scenario $SCENARIO}
        ;;
    
    "analyze")
        echo -e "${BLUE}Analyzing previous test results...${NC}"
        RESULT_FILE="${SCENARIO:-}"
        
        if [ -z "$RESULT_FILE" ]; then
            # Interactive selection
            select_result_file
        elif [ "$RESULT_FILE" = "latest" ]; then
            # Get the most recent file
            RESULT_FILE=$(ls -t comprehensive-results/*.json 2>/dev/null | head -1 | xargs basename 2>/dev/null)
            if [ -z "$RESULT_FILE" ]; then
                echo -e "${RED}No result files found${NC}"
                exit 1
            fi
            echo -e "${YELLOW}Using most recent: $RESULT_FILE${NC}"
        elif [ "$RESULT_FILE" = "all" ]; then
            echo -e "${YELLOW}Analyzing all results...${NC}"
            python3 comprehensive_test_runner.py \
                --api-key "$KUBENTLY_API_KEY" \
                --analyze-previous all
            exit 0
        fi
        
        # Analyze the selected/specified file
        if [ -n "$RESULT_FILE" ]; then
            echo -e "${YELLOW}Analyzing: $RESULT_FILE${NC}"
            python3 comprehensive_test_runner.py \
                --api-key "$KUBENTLY_API_KEY" \
                --analyze-previous "$RESULT_FILE"
        fi
        ;;
    
    "test-and-analyze")
        echo -e "${BLUE}Running all tests with automatic analysis...${NC}"
        echo -e "${YELLOW}Note: This will run all scenarios and analyze each result${NC}\n"
        
        # Run tests with auto-analyze flag
        python3 comprehensive_test_runner.py \
            --api-url "$API_URL" \
            --api-key "$KUBENTLY_API_KEY" \
            --auto-analyze \
            ${SCENARIO:+--scenario $SCENARIO}
        ;;
    
    "recent")
        echo -e "${BLUE}Recent test results:${NC}\n"
        
        # List recent results with details
        results=($(ls -t comprehensive-results/*.json 2>/dev/null | head -20))
        
        if [ ${#results[@]} -eq 0 ]; then
            echo -e "${RED}No result files found${NC}"
            exit 1
        fi
        
        echo -e "${YELLOW}Most recent test results:${NC}\n"
        
        for result_path in "${results[@]}"; do
            result_name=$(basename "$result_path")
            # Parse the filename
            scenario=$(echo "$result_name" | cut -d'.' -f1)
            time_part=$(echo "$result_name" | cut -d'.' -f2)
            date_part=$(echo "$result_name" | cut -d'.' -f3)
            
            # Format date nicely
            if [[ $date_part =~ ^([0-9]{4})([0-9]{2})([0-9]{2})$ ]]; then
                # Old format: YYYYMMDD -> MM-DD-YYYY
                formatted_date="${BASH_REMATCH[2]}-${BASH_REMATCH[3]}-${BASH_REMATCH[1]}"
            else
                # New format: already MM-DD-YYYY
                formatted_date=$date_part
            fi
            
            # Get test details from JSON
            if command -v jq &> /dev/null; then
                root_cause=$(jq -r '.analysis.root_cause_analysis.identified_correctly // "N/A"' "$result_path" 2>/dev/null)
                duration=$(jq -r '.debug_trace.duration_seconds // "N/A"' "$result_path" 2>/dev/null | xargs printf "%.2f")
                
                # Color code based on success
                if [ "$root_cause" = "true" ]; then
                    status="${GREEN}✓${NC}"
                elif [ "$root_cause" = "false" ]; then
                    status="${RED}✗${NC}"
                else
                    status="${YELLOW}?${NC}"
                fi
                
                printf "%s %-35s  %s @ %s  (%.2fs)\n" "$status" "$scenario" "$formatted_date" "$time_part" "$duration"
            else
                printf "  %-35s  %s @ %s\n" "$scenario" "$formatted_date" "$time_part"
            fi
        done
        
        echo ""
        echo -e "${YELLOW}To analyze a result:${NC}"
        echo "  $0 analyze                    # Interactive selection"
        echo "  $0 analyze <filename>         # Specific file"
        echo "  $0 analyze latest             # Most recent result"
        ;;
    
    *)
        echo -e "${RED}Unknown command: $TEST_TYPE${NC}"
        echo -e "Usage: $0 [test|analyze|test-and-analyze|recent] [scenario_name|result_file] [--api-key YOUR_KEY]"
        echo -e ""
        echo -e "Commands:"
        echo -e "  test               Run comprehensive tests"
        echo -e "  analyze            Analyze previous test results"
        echo -e "  test-and-analyze   Run all tests and automatically analyze results"
        echo -e "  recent             List recent test results"
        echo -e ""
        echo -e "Examples:"
        echo -e "  $0 test                             # Run tests (interactive selection)"
        echo -e "  $0 test 01-imagepullbackoff         # Run specific scenario"
        echo -e "  $0 test-and-analyze                 # Run all tests and analyze each"
        echo -e "  $0 test-and-analyze 03-crashloop    # Run specific test and analyze"
        echo -e "  $0 analyze                          # Analyze results (interactive selection)"
        echo -e "  $0 analyze latest                   # Analyze most recent result"
        echo -e "  $0 analyze all                      # Analyze all results"
        echo -e "  $0 recent                           # List recent results with status"
        echo -e ""
        echo -e "Note: --api-key can be used with any command to avoid interactive prompt"
        exit 1
        ;;
esac

echo -e "\n${GREEN}Testing complete!${NC}"
echo -e "${BLUE}Results are stored in:${NC}"
echo -e "  - comprehensive-results/       # Test results and summaries"
echo -e "  - comprehensive-results/logs/  # Detailed logs"
echo -e "  - comprehensive-results/traces/ # Debug traces with tool calls"
echo -e "  - comprehensive-results/analysis/ # Gemini AI analysis"
echo -e ""
echo -e "${YELLOW}View the latest report:${NC}"
echo -e "  find comprehensive-results -name '*.json' -type f -exec ls -lt {} + | head -5"