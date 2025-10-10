#!/bin/bash
# Unified test runner for Kubently with analysis

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
COMMAND=""
API_KEY=""
API_URL="http://localhost:8080"
SCENARIO=""
SKIP_ANALYSIS=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        test-and-analyze)
            COMMAND="test-and-analyze"
            shift
            ;;
        test-only)
            COMMAND="test-only"
            SKIP_ANALYSIS=true
            shift
            ;;
        analyze-only)
            COMMAND="analyze-only"
            shift
            ;;
        rerun-failed)
            COMMAND="rerun-failed"
            shift
            ;;
        --api-key)
            API_KEY="$2"
            shift 2
            ;;
        --api-url)
            API_URL="$2"
            shift 2
            ;;
        --scenario)
            SCENARIO="$2"
            shift 2
            ;;
        --from)
            RERUN_FROM="$2"
            shift 2
            ;;
        --full-analysis)
            FULL_ANALYSIS=true
            shift
            ;;
        --help)
            echo "Usage: $0 [command] [options]"
            echo ""
            echo "Commands:"
            echo "  test-and-analyze  Run tests and analyze results (default)"
            echo "  test-only        Run tests without analysis"
            echo "  analyze-only     Analyze existing results"
            echo "  rerun-failed     Re-run only failed tests from latest or specified results"
            echo ""
            echo "Options:"
            echo "  --api-key KEY    API key for Kubently (required for testing)"
            echo "  --api-url URL    API URL (default: http://localhost:8080)"
            echo "  --scenario NAME  Run specific scenario only"
            echo "  --from PATH      Results file/dir to rerun failed tests from (for rerun-failed)"
            echo "  --full-analysis  Run full improvement analysis (default: RCA-only)"
            echo ""
            echo "Interactive Mode:"
            echo "  $0 --api-key test-key        # Shows scenario selector menu"
            echo ""
            echo "Examples:"
            echo "  $0 --api-key test-key                                         # Interactive mode"
            echo "  $0 test-and-analyze --api-key test-key                       # Run all scenarios"
            echo "  $0 test-only --api-key test-key --scenario 01-imagepullbackoff-typo"
            echo "  $0 analyze-only                                              # Interactive result selector"
            echo "  $0 rerun-failed --api-key test-key                           # Re-run failed from latest"
            echo "  $0 rerun-failed --api-key test-key --from test-results-20241210_143022/report.json"
            echo "  $0 rerun-failed --api-key test-key --from test-results-20241210_143022/"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Function to select result directory for analysis
select_result_dir() {
    echo -e "${BLUE}Select test results to analyze:${NC}\n"
    
    # List available result directories
    result_dirs=($(ls -d test-results-* 2>/dev/null | sort -r))
    
    if [ ${#result_dirs[@]} -eq 0 ]; then
        echo -e "${RED}No result directories found${NC}"
        echo "Run tests first with: $0 --api-key KEY"
        exit 1
    fi
    
    echo -e "${YELLOW}Available result directories (most recent first):${NC}"
    for i in "${!result_dirs[@]}"; do
        dir="${result_dirs[$i]}"
        # Extract timestamp from directory name
        timestamp=$(echo "$dir" | sed 's/test-results-//')
        # Count scenarios in directory
        count=$(ls "$dir"/*.json 2>/dev/null | grep -v summary.json | grep -v "/traces/" | wc -l | tr -d ' ')
        
        printf "${YELLOW}%2d)${NC} %-30s (%s scenarios)\n" $((i+1)) "$dir" "$count"
    done
    
    echo ""
    read -p "Select result directory (1-${#result_dirs[@]}): " selection
    
    if [[ "$selection" =~ ^[0-9]+$ ]] && [ "$selection" -ge 1 ] && [ "$selection" -le "${#result_dirs[@]}" ]; then
        RESULTS_DIR="${result_dirs[$((selection-1))]}"
        echo -e "${GREEN}Selected: $RESULTS_DIR${NC}"
    else
        echo -e "${RED}Invalid selection${NC}"
        exit 1
    fi
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
    echo "  - Type 'a' to run all and analyze"
    echo ""
    read -p "Your choice: " selection
    
    if [ -z "$selection" ]; then
        echo -e "${GREEN}Running all scenarios...${NC}"
        SCENARIO=""
    elif [ "$selection" = "a" ] || [ "$selection" = "A" ]; then
        echo -e "${GREEN}Running all scenarios with analysis...${NC}"
        SCENARIO=""
        COMMAND="test-and-analyze"
    elif [[ "$selection" =~ ^[0-9]+$ ]] && [ "$selection" -ge 1 ] && [ "$selection" -le "${#scenarios[@]}" ]; then
        SCENARIO="${scenarios[$((selection-1))]}"
        echo -e "${GREEN}Selected scenario: $SCENARIO${NC}"
        COMMAND="test-and-analyze"
    else
        echo -e "${RED}Invalid selection${NC}"
        exit 1
    fi
}

# Default to test-and-analyze if no command specified
if [ -z "$COMMAND" ]; then
    # If API key is provided but no specific command or scenario, show interactive menu
    if [ -n "$API_KEY" ] && [ -z "$SCENARIO" ]; then
        COMMAND="interactive"
    else
        COMMAND="test-and-analyze"
    fi
fi

echo -e "${GREEN}=== Kubently Test Automation ===${NC}"
echo -e "${BLUE}Command: ${COMMAND}${NC}"
echo ""

# Check Python version
PYTHON_CMD=""
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo -e "${RED}Error: Python not found${NC}"
    exit 1
fi

# Check/create virtual environment
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    $PYTHON_CMD -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install/update dependencies
echo -e "${GREEN}Checking dependencies...${NC}"
pip install -q --upgrade pip
pip install -q httpx rich pyyaml python-dotenv a2a-sdk

# Install Gemini if available for analysis
if [ "$SKIP_ANALYSIS" = false ]; then
    pip install -q google-generativeai 2>/dev/null || {
        echo -e "${RED}Error: google-generativeai not installed. Analysis requires this package.${NC}"
        exit 1
    }
fi

# Function to find latest results directory
find_latest_results() {
    # Exclude rerun directories when looking for latest
    ls -d test-results-* 2>/dev/null | grep -v "test-results-rerun" | sort -r | head -1
}

# Handle different commands
case $COMMAND in
    interactive)
        # Interactive mode - select scenario first
        if [ -z "$API_KEY" ]; then
            echo -e "${RED}Error: --api-key is required${NC}"
            echo "Use --help for usage information"
            exit 1
        fi
        
        # Only select scenario if one wasn't provided on command line
        if [ -z "$ORIGINAL_SCENARIO" ]; then
            select_scenario
        else
            SCENARIO="$ORIGINAL_SCENARIO"
        fi
        
        # Now run with the selected scenario and command
        # Continue to test-and-analyze processing below
        ;;
esac

# Now handle the actual testing/analysis commands
case $COMMAND in
    test-and-analyze|test-only|interactive|rerun-failed)
        # Check required parameters for testing
        if [ -z "$API_KEY" ]; then
            echo -e "${RED}Error: --api-key is required for testing${NC}"
            echo "Use --help for usage information"
            exit 1
        fi
        
        # Check if Kubently is running
        echo -e "${GREEN}Checking Kubently service...${NC}"
        if ! curl -s "$API_URL/health" > /dev/null 2>&1; then
            echo -e "${RED}Error: Kubently is not running on $API_URL${NC}"
            echo "Please ensure Kubently is deployed and accessible"
            exit 1
        fi
        echo -e "${GREEN}✓ Kubently service is healthy${NC}"
        echo ""
        
        # Handle rerun-failed specially
        if [ "$COMMAND" = "rerun-failed" ]; then
            echo -e "${GREEN}Finding failed tests...${NC}"

            # Determine source of results
            if [ -n "$RERUN_FROM" ]; then
                # User specified a file or directory
                if [ -f "$RERUN_FROM" ]; then
                    # It's a file - check if it's report.json or a scenario result
                    if [[ "$RERUN_FROM" == *"report.json" ]]; then
                        # Extract directory from report.json path
                        RESULTS_SOURCE=$(dirname "$RERUN_FROM")
                    else
                        # Assume it's a scenario result file
                        RESULTS_SOURCE=$(dirname "$RERUN_FROM")
                    fi
                elif [ -d "$RERUN_FROM" ]; then
                    # It's a directory
                    RESULTS_SOURCE="$RERUN_FROM"
                else
                    echo -e "${RED}Error: Specified path does not exist: $RERUN_FROM${NC}"
                    exit 1
                fi
                echo -e "${BLUE}Using results from: $RESULTS_SOURCE${NC}"
            else
                # Find the latest test results directory
                RESULTS_SOURCE=$(find_latest_results)
                if [ -z "$RESULTS_SOURCE" ]; then
                    echo -e "${RED}No test results found to rerun${NC}"
                    exit 1
                fi
                echo -e "${BLUE}Using latest results from: $RESULTS_SOURCE${NC}"
            fi

            # Find failed scenarios
            FAILED_SCENARIOS=""
            for json_file in "$RESULTS_SOURCE"/*.json; do
                if [ -f "$json_file" ] && [ "$(basename "$json_file")" != "summary.json" ]; then
                    # Check if scenario failed
                    if grep -q '"success": false' "$json_file" 2>/dev/null; then
                        scenario_name=$(basename "$json_file" .json)
                        if [ -z "$FAILED_SCENARIOS" ]; then
                            FAILED_SCENARIOS="$scenario_name"
                        else
                            FAILED_SCENARIOS="$FAILED_SCENARIOS,$scenario_name"
                        fi
                    fi
                fi
            done

            if [ -z "$FAILED_SCENARIOS" ]; then
                echo -e "${GREEN}✓ No failed tests found. All tests passed!${NC}"
                exit 0
            fi

            echo -e "${YELLOW}Failed scenarios to rerun: ${FAILED_SCENARIOS/,/, }${NC}"
            echo ""

            # Run only the failed scenarios
            echo -e "${GREEN}Re-running failed tests...${NC}"

            # Create a new results directory for rerun
            RERUN_DIR="test-results-rerun-$(date +%Y%m%d_%H%M%S)"
            mkdir -p "$RERUN_DIR"

            # Run each failed scenario individually
            IFS=',' read -ra SCENARIOS <<< "$FAILED_SCENARIOS"

            # Prepare flags for rerun
            RERUN_FLAGS=""
            if [ "$FULL_ANALYSIS" = true ]; then
                RERUN_FLAGS="--full-analysis"
            fi

            for scenario in "${SCENARIOS[@]}"; do
                echo -e "${BLUE}Re-running scenario: $scenario${NC}"
                $PYTHON_CMD test_runner.py \
                    --api-url "$API_URL" \
                    --api-key "$API_KEY" \
                    --scenario "$scenario" \
                    --results-dir "$RERUN_DIR" \
                    $RERUN_FLAGS
            done

            # Update RESULTS_DIR for analysis
            RESULTS_DIR="$RERUN_DIR"

            echo -e "${GREEN}✓ Rerun completed. Results saved to: $RERUN_DIR${NC}"
        else
            # Regular test run
            echo -e "${GREEN}Running tests...${NC}"

            # Add skip-analysis flag if in test-only mode
            EXTRA_ARGS=""
            if [ "$SKIP_ANALYSIS" = true ]; then
                EXTRA_ARGS="--skip-analysis"
            elif [ "$FULL_ANALYSIS" = true ]; then
                EXTRA_ARGS="--full-analysis"
            fi

            if [ -n "$SCENARIO" ]; then
                echo -e "${BLUE}Testing scenario: $SCENARIO${NC}"
                $PYTHON_CMD test_runner.py \
                    --api-url "$API_URL" \
                    --api-key "$API_KEY" \
                    --scenario "$SCENARIO" \
                    $EXTRA_ARGS
            else
                echo -e "${BLUE}Testing all scenarios${NC}"
                $PYTHON_CMD test_runner.py \
                    --api-url "$API_URL" \
                    --api-key "$API_KEY" \
                    $EXTRA_ARGS
            fi
        fi

        # Get the results directory that was just created (unless already set by rerun)
        if [ -z "$RESULTS_DIR" ]; then
            RESULTS_DIR=$(find_latest_results)
        fi

        if [ -z "$RESULTS_DIR" ]; then
            echo -e "${RED}Error: No results directory found${NC}"
            exit 1
        fi

        echo ""
        echo -e "${GREEN}✓ Tests complete. Results saved to: $RESULTS_DIR${NC}"
        
        # Run analysis if not skipped
        if [ "$SKIP_ANALYSIS" = false ]; then
            echo ""
            echo -e "${GREEN}Running analysis...${NC}"
            
            # Check for Gemini API key
            if [ -z "$GOOGLE_API_KEY" ]; then
                echo -e "${RED}Error: GOOGLE_API_KEY not set. Analysis requires Gemini API.${NC}"
                echo "Please set: export GOOGLE_API_KEY=your_key"
                exit 1
            fi
            
            # Create analysis directory
            mkdir -p "$RESULTS_DIR/analysis"
            
            # Run analyzer
            if [ -n "$SCENARIO" ]; then
                # Analyze specific scenario result
                $PYTHON_CMD analyzer.py \
                    --result-file "$RESULTS_DIR/$SCENARIO.json" \
                    --output "$RESULTS_DIR/analysis/report.md"
            else
                # Analyze all results in directory
                $PYTHON_CMD analyzer.py \
                    --result-dir "$RESULTS_DIR" \
                    --output "$RESULTS_DIR/analysis/report.md"
            fi
            
            echo ""
            echo -e "${GREEN}✓ Analysis complete${NC}"
            echo ""
            echo "Reports generated:"
            echo "  - Test results: $RESULTS_DIR/"
            echo "  - Analysis report: $RESULTS_DIR/analysis/report.md"
            echo "  - JSON report: $RESULTS_DIR/analysis/report.json"
            
            # Show key recommendations if available
            if [ -f "$RESULTS_DIR/analysis/report.md" ]; then
                echo ""
                echo -e "${YELLOW}Top Recommendations:${NC}"
                grep -A 2 "^#### CRITICAL:" "$RESULTS_DIR/analysis/report.md" 2>/dev/null | head -10 || {
                    grep -A 2 "^### " "$RESULTS_DIR/analysis/report.md" 2>/dev/null | head -10 || true
                }
            fi
        fi
        ;;
        
    analyze-only)
        # Find results directory
        if [ -n "$SCENARIO" ]; then
            # If scenario specified, use it as results directory
            RESULTS_DIR="$SCENARIO"
        else
            # Interactive selection
            select_result_dir
        fi
        
        if [ -z "$RESULTS_DIR" ] || [ ! -d "$RESULTS_DIR" ]; then
            echo -e "${RED}Error: Results directory not found: $RESULTS_DIR${NC}"
            exit 1
        fi
        
        echo -e "${GREEN}Analyzing results from: $RESULTS_DIR${NC}"
        
        # Check for Gemini API key
        if [ -z "$GOOGLE_API_KEY" ]; then
            echo -e "${RED}Error: GOOGLE_API_KEY not set. Analysis requires Gemini API.${NC}"
            echo "Please set: export GOOGLE_API_KEY=your_key"
            exit 1
        fi
        
        # Create analysis directory
        mkdir -p "$RESULTS_DIR/analysis"
        
        # Run analyzer
        $PYTHON_CMD analyzer.py \
            --result-dir "$RESULTS_DIR" \
            --output "$RESULTS_DIR/analysis/report.md"
        
        echo ""
        echo -e "${GREEN}✓ Analysis complete${NC}"
        echo ""
        echo "Reports generated:"
        echo "  - Analysis report: $RESULTS_DIR/analysis/report.md"
        echo "  - JSON report: $RESULTS_DIR/analysis/report.json"
        ;;
esac

echo ""
echo -e "${GREEN}Done!${NC}"
echo ""

# Deactivate virtual environment
deactivate 2>/dev/null || true