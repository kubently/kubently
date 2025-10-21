#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}Kubently LangSmith Experiment Runner${NC}"
echo "======================================"

# Check for required environment variables
check_env() {
    if [ -z "${LANGSMITH_API_KEY}" ]; then
        echo -e "${YELLOW}Warning: LANGSMITH_API_KEY not set${NC}"
        echo "Please set: export LANGSMITH_API_KEY=your-key"
        echo ""
    fi

    if [ -z "${GOOGLE_API_KEY}" ]; then
        echo -e "${YELLOW}Warning: GOOGLE_API_KEY not set${NC}"
        echo "Gemini models will not be available"
        echo ""
    fi
}

# Install dependencies if needed
install_deps() {
    echo "Checking dependencies..."
    if ! python3 -c "import langsmith" 2>/dev/null; then
        echo "Installing required packages..."
        pip install -q -r requirements.txt
        echo -e "${GREEN}✓ Dependencies installed${NC}"
    else
        echo -e "${GREEN}✓ Dependencies already installed${NC}"
    fi
}

# Parse command line arguments
ACTION=${1:-help}
DATASET_NAME=${2:-kubently-scenarios}

case $ACTION in
    setup)
        echo "Setting up LangSmith experiments..."
        check_env
        install_deps
        echo -e "\n${GREEN}Setup complete!${NC}"
        echo "Next steps:"
        echo "  1. Export your API keys:"
        echo "     export LANGSMITH_API_KEY=your-key"
        echo "     export GOOGLE_API_KEY=your-key"
        echo "  2. Build dataset: ./run_experiment.sh build-dataset"
        echo "  3. Run experiment: ./run_experiment.sh run"
        ;;

    build-dataset)
        echo "Building LangSmith dataset from scenarios..."
        check_env

        if [ -z "${LANGSMITH_API_KEY}" ]; then
            echo -e "${RED}Error: LANGSMITH_API_KEY is required${NC}"
            exit 1
        fi

        # Build dataset
        python3 dataset_builder.py --dataset-name "$DATASET_NAME"

        # Also export to JSON for backup
        python3 dataset_builder.py --export-json --json-file "${DATASET_NAME}.json"

        echo -e "\n${GREEN}Dataset created successfully!${NC}"
        echo "Dataset name: $DATASET_NAME"
        echo "View in LangSmith: https://smith.langchain.com/"
        ;;

    run)
        echo "Running experiment..."
        check_env

        if [ -z "${LANGSMITH_API_KEY}" ]; then
            echo -e "${RED}Error: LANGSMITH_API_KEY is required${NC}"
            exit 1
        fi

        # Check if Kubently is running
        if ! curl -s http://localhost:8080/health > /dev/null 2>&1; then
            echo -e "${YELLOW}Warning: Kubently API not accessible at localhost:8080${NC}"
            echo "Please ensure Kubently is deployed and port-forwarded"
            echo "Run: kubectl port-forward -n kubently svc/kubently-api 8080:8080"
            exit 1
        fi

        # Run experiment
        EXPERIMENT_PREFIX="kubently-${USER}-$(date +%Y%m%d)"

        echo "Running experiments on dataset: $DATASET_NAME"
        echo "Experiment prefix: $EXPERIMENT_PREFIX"

        python3 experiment_runner.py \
            --dataset "$DATASET_NAME" \
            --api-url http://localhost:8080 \
            --api-key test-api-key \
            --experiment-prefix "$EXPERIMENT_PREFIX"
        ;;

    run-single)
        SCENARIO_NAME=${3:-01-imagepullbackoff-typo}
        echo "Running single scenario experiment: $SCENARIO_NAME"
        check_env

        # Create a single-scenario dataset
        SINGLE_DATASET="${DATASET_NAME}-single-${SCENARIO_NAME}"

        # This would need a modified dataset builder - for now use full dataset
        python3 experiment_runner.py \
            --dataset "$DATASET_NAME" \
            --api-url http://localhost:8080 \
            --api-key test-api-key \
            --experiment-prefix "single-$SCENARIO_NAME"
        ;;

    compare)
        echo "Running comparison experiment with multiple prompt variations..."
        check_env

        # Create configs file with variations
        cat > experiment_configs.json << 'EOF'
[
    {
        "prompt_template": "Query: {query}\nNamespace: {namespace}\n\nIdentify and fix the issue.",
        "model_name": "gemini-1.5-flash",
        "model_provider": "gemini",
        "temperature": 0.3,
        "metadata": {"variant": "minimal"}
    },
    {
        "prompt_template": "You are a Kubernetes expert. Query: {query}\nNamespace: {namespace}\n\n1. Diagnose the issue\n2. Provide the fix\n3. Verify the solution",
        "model_name": "gemini-1.5-flash",
        "model_provider": "gemini",
        "temperature": 0.3,
        "metadata": {"variant": "structured"}
    },
    {
        "prompt_template": "Debug this Kubernetes problem step by step:\n\nQuery: {query}\nNamespace: {namespace}\n\nUse all available tools to investigate. Provide root cause analysis and specific kubectl commands to fix.",
        "model_name": "gemini-1.5-flash",
        "model_provider": "gemini",
        "temperature": 0.5,
        "metadata": {"variant": "detailed"}
    }
]
EOF

        python3 experiment_runner.py \
            --dataset "$DATASET_NAME" \
            --api-url http://localhost:8080 \
            --api-key test-api-key \
            --experiment-prefix "comparison" \
            --configs experiment_configs.json
        ;;

    results)
        echo "Recent experiment results:"
        ls -la experiment_results_*.json 2>/dev/null || echo "No results found"

        if [ -f experiment_results_*.json ]; then
            echo -e "\n${GREEN}Latest results:${NC}"
            latest=$(ls -t experiment_results_*.json | head -1)
            python3 -c "import json; import sys; data=json.load(open('$latest')); print(json.dumps(data, indent=2)[:1000])"
        fi
        ;;

    clean)
        echo "Cleaning up experiment artifacts..."
        rm -f experiment_results_*.json
        rm -f experiment_configs.json
        rm -f *.json.backup
        echo -e "${GREEN}✓ Cleanup complete${NC}"
        ;;

    help|*)
        echo "Usage: ./run_experiment.sh [command] [options]"
        echo ""
        echo "Commands:"
        echo "  setup           - Install dependencies and check environment"
        echo "  build-dataset   - Build LangSmith dataset from test scenarios"
        echo "  run            - Run experiment with default configurations"
        echo "  run-single     - Run experiment on single scenario"
        echo "  compare        - Run comparison with multiple prompt variations"
        echo "  results        - Show recent experiment results"
        echo "  clean          - Clean up experiment artifacts"
        echo "  help           - Show this help message"
        echo ""
        echo "Environment variables:"
        echo "  LANGSMITH_API_KEY - Required for LangSmith"
        echo "  GOOGLE_API_KEY    - Required for Gemini models"
        echo "  ANTHROPIC_API_KEY - Optional for Claude models"
        echo "  OPENAI_API_KEY    - Optional for GPT models"
        echo ""
        echo "Examples:"
        echo "  ./run_experiment.sh setup"
        echo "  ./run_experiment.sh build-dataset"
        echo "  ./run_experiment.sh run"
        echo "  ./run_experiment.sh compare"
        ;;
esac