#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}Kubently Integrated LangSmith Experiment Runner${NC}"
echo "================================================"
echo "This runner automatically manages scenario setup/teardown"
echo ""

# Check for required environment variables
check_env() {
    if [ -z "${LANGSMITH_API_KEY}" ]; then
        echo -e "${RED}Error: LANGSMITH_API_KEY not set${NC}"
        echo "Please set: export LANGSMITH_API_KEY=your-key"
        exit 1
    fi

    if [ -z "${GOOGLE_API_KEY}" ]; then
        echo -e "${YELLOW}Warning: GOOGLE_API_KEY not set${NC}"
        echo "Gemini models will not be available"
    fi

    # Check if kubectl is configured
    if ! kubectl cluster-info &>/dev/null; then
        echo -e "${RED}Error: kubectl not configured or cluster not accessible${NC}"
        echo "Please ensure you have a Kubernetes cluster running (e.g., Kind)"
        exit 1
    fi
}

# Check if Kubently is running
check_kubently() {
    if ! curl -s http://localhost:8080/health > /dev/null 2>&1; then
        echo -e "${RED}Error: Kubently API not accessible at localhost:8080${NC}"
        echo ""
        echo "To deploy Kubently:"
        echo "  1. cd .."
        echo "  2. ./deploy-test.sh"
        echo "  3. kubectl port-forward -n kubently svc/kubently-api 8080:8080 &"
        exit 1
    fi
    echo -e "${GREEN}✓ Kubently API is accessible${NC}"
}

# Install dependencies if needed
install_deps() {
    if ! python3 -c "import langsmith" 2>/dev/null; then
        echo "Installing required packages..."
        pip install -q -r requirements.txt
        echo -e "${GREEN}✓ Dependencies installed${NC}"
    fi
}

# Parse command line arguments
ACTION=${1:-help}
DATASET_NAME=${2:-kubently-scenarios}

case $ACTION in
    test-single)
        SCENARIO=${3:-01-imagepullbackoff-typo}
        echo -e "${BLUE}Running single scenario: $SCENARIO${NC}"
        check_env
        check_kubently
        install_deps

        python3 integrated_experiment_runner.py \
            --dataset "$DATASET_NAME" \
            --scenarios "$SCENARIO" \
            --api-url http://localhost:8080 \
            --api-key test-api-key \
            --max-concurrent 1
        ;;

    test-batch)
        # Run a batch of specific scenarios
        SCENARIOS=${3:-"01-imagepullbackoff-typo 02-imagepullbackoff-private 03-crashloopbackoff"}
        echo -e "${BLUE}Running batch test with scenarios:${NC}"
        echo "$SCENARIOS" | tr ' ' '\n' | sed 's/^/  - /'
        check_env
        check_kubently
        install_deps

        python3 integrated_experiment_runner.py \
            --dataset "$DATASET_NAME" \
            --scenarios $SCENARIOS \
            --api-url http://localhost:8080 \
            --api-key test-api-key \
            --max-concurrent 2
        ;;

    test-all)
        echo -e "${BLUE}Running all scenarios${NC}"
        check_env
        check_kubently
        install_deps

        python3 integrated_experiment_runner.py \
            --dataset "$DATASET_NAME" \
            --api-url http://localhost:8080 \
            --api-key test-api-key \
            --max-concurrent 2
        ;;

    quick-test)
        # Quick test with just 3 easy scenarios
        echo -e "${BLUE}Running quick test (3 easy scenarios)${NC}"
        check_env
        check_kubently
        install_deps

        python3 integrated_experiment_runner.py \
            --dataset "$DATASET_NAME" \
            --scenarios "01-imagepullbackoff-typo" "03-crashloopbackoff" "06-oomkilled" \
            --api-url http://localhost:8080 \
            --api-key test-api-key \
            --max-concurrent 2 \
            --temperature 0.3
        ;;

    compare-models)
        echo -e "${BLUE}Comparing different models${NC}"
        check_env
        check_kubently
        install_deps

        SCENARIOS="01-imagepullbackoff-typo 03-crashloopbackoff"

        # Test with Gemini Flash
        echo -e "\n${YELLOW}Testing Gemini 1.5 Flash...${NC}"
        python3 integrated_experiment_runner.py \
            --dataset "$DATASET_NAME" \
            --scenarios $SCENARIOS \
            --model "gemini-1.5-flash" \
            --temperature 0.3 \
            --max-concurrent 1

        # Test with Gemini Pro if available
        if [ ! -z "${GOOGLE_API_KEY}" ]; then
            echo -e "\n${YELLOW}Testing Gemini 1.5 Pro...${NC}"
            python3 integrated_experiment_runner.py \
                --dataset "$DATASET_NAME" \
                --scenarios $SCENARIOS \
                --model "gemini-1.5-pro" \
                --temperature 0.3 \
                --max-concurrent 1
        fi

        # Test with Claude if available
        if [ ! -z "${ANTHROPIC_API_KEY}" ]; then
            echo -e "\n${YELLOW}Testing Claude 3.5 Sonnet...${NC}"
            python3 integrated_experiment_runner.py \
                --dataset "$DATASET_NAME" \
                --scenarios $SCENARIOS \
                --model "claude-3-5-sonnet-20240620" \
                --temperature 0.3 \
                --max-concurrent 1
        fi
        ;;

    verify-scenarios)
        echo -e "${BLUE}Verifying scenario scripts can setup/cleanup${NC}"

        # Test one scenario setup and cleanup
        SCENARIO_PATH="../test-automation/scenarios/01-imagepullbackoff-typo.sh"

        if [ -f "$SCENARIO_PATH" ]; then
            echo "Testing scenario: 01-imagepullbackoff-typo"

            # Setup
            echo -n "  Setting up... "
            if bash "$SCENARIO_PATH" setup > /dev/null 2>&1; then
                echo -e "${GREEN}✓${NC}"
            else
                echo -e "${RED}✗${NC}"
            fi

            # Check
            echo -n "  Checking namespace... "
            if kubectl get ns test-scenario-1 > /dev/null 2>&1; then
                echo -e "${GREEN}✓${NC}"
            else
                echo -e "${RED}✗${NC}"
            fi

            # Cleanup
            echo -n "  Cleaning up... "
            if bash "$SCENARIO_PATH" cleanup > /dev/null 2>&1; then
                echo -e "${GREEN}✓${NC}"
            else
                echo -e "${RED}✗${NC}"
            fi
        else
            echo -e "${RED}Scenario script not found${NC}"
        fi
        ;;

    status)
        echo "System Status:"
        echo ""

        # Check environment
        echo -n "LangSmith API Key: "
        [ ! -z "${LANGSMITH_API_KEY}" ] && echo -e "${GREEN}✓${NC}" || echo -e "${RED}✗${NC}"

        echo -n "Google API Key: "
        [ ! -z "${GOOGLE_API_KEY}" ] && echo -e "${GREEN}✓${NC}" || echo -e "${YELLOW}⚠${NC}"

        echo -n "Anthropic API Key: "
        [ ! -z "${ANTHROPIC_API_KEY}" ] && echo -e "${GREEN}✓${NC}" || echo -e "${YELLOW}⚠${NC}"

        # Check Kubernetes
        echo -n "Kubernetes Cluster: "
        kubectl cluster-info &>/dev/null && echo -e "${GREEN}✓${NC}" || echo -e "${RED}✗${NC}"

        # Check Kubently
        echo -n "Kubently API: "
        curl -s http://localhost:8080/health > /dev/null 2>&1 && echo -e "${GREEN}✓${NC}" || echo -e "${RED}✗${NC}"

        # Check scenarios
        echo -n "Test Scenarios: "
        SCENARIO_COUNT=$(ls ../test-automation/scenarios/*.sh 2>/dev/null | wc -l)
        if [ "$SCENARIO_COUNT" -gt 0 ]; then
            echo -e "${GREEN}✓ ($SCENARIO_COUNT scenarios)${NC}"
        else
            echo -e "${RED}✗${NC}"
        fi
        ;;

    clean)
        echo "Cleaning up any leftover test namespaces..."

        # Clean up test namespaces
        for ns in $(kubectl get ns -o name | grep -E 'test-ns-|test-scenario-' | cut -d/ -f2); do
            echo "  Deleting namespace: $ns"
            kubectl delete namespace "$ns" --ignore-not-found=true &
        done

        wait
        echo -e "${GREEN}✓ Cleanup complete${NC}"
        ;;

    help|*)
        echo "Usage: ./run_integrated.sh [command] [dataset] [options]"
        echo ""
        echo "Commands:"
        echo "  test-single [dataset] [scenario]  - Test a single scenario"
        echo "  test-batch [dataset] [scenarios]  - Test specific scenarios"
        echo "  test-all [dataset]                - Test all scenarios"
        echo "  quick-test                        - Quick test with 3 easy scenarios"
        echo "  compare-models                    - Compare different LLM models"
        echo "  verify-scenarios                  - Verify scenario scripts work"
        echo "  status                           - Show system status"
        echo "  clean                            - Clean up test namespaces"
        echo "  help                             - Show this help"
        echo ""
        echo "Examples:"
        echo "  ./run_integrated.sh test-single kubently-scenarios 01-imagepullbackoff-typo"
        echo "  ./run_integrated.sh test-batch kubently-scenarios \"01-imagepullbackoff-typo 03-crashloopbackoff\""
        echo "  ./run_integrated.sh quick-test"
        echo "  ./run_integrated.sh compare-models"
        echo ""
        echo "Environment variables:"
        echo "  LANGSMITH_API_KEY - Required for LangSmith"
        echo "  GOOGLE_API_KEY    - Required for Gemini models"
        echo "  ANTHROPIC_API_KEY - Optional for Claude models"
        echo "  OPENAI_API_KEY    - Optional for GPT models"
        ;;
esac