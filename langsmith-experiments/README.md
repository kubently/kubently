# Kubently LangSmith Experiments

This directory contains the LangSmith integration for Kubently, enabling systematic testing and evaluation of different prompts, tools, and model configurations.

## Overview

The LangSmith integration replaces the manual test-automation process with a more structured approach that:
- Converts test scenarios into LangSmith datasets
- Runs experiments with different prompt templates and model configurations
- Evaluates responses against expected fixes
- Tracks performance metrics and provides detailed analysis
- Enables easy comparison between different approaches

### Two Execution Modes

1. **Standard Mode** (`experiment_runner.py`): Tests against existing Kubernetes issues
   - Requires manual scenario setup before running experiments
   - Useful when you have persistent test environments
   - Lower overhead for multiple experiments on same scenarios

2. **Integrated Mode** (`integrated_experiment_runner.py`): Automatic scenario lifecycle
   - Automatically sets up Kubernetes scenarios before each test
   - Runs the experiment against real issues
   - Cleans up scenarios after each test
   - Perfect for CI/CD and automated testing

## Quick Start

### 1. Setup Environment

```bash
# Install dependencies
./run_experiment.sh setup

# Set required API keys
export LANGSMITH_API_KEY=your-langsmith-key
export GOOGLE_API_KEY=your-gemini-key

# Optional: Add other model providers
export ANTHROPIC_API_KEY=your-anthropic-key
export OPENAI_API_KEY=your-openai-key
```

### 2. Build Dataset

Convert existing test scenarios to LangSmith dataset:

```bash
./run_experiment.sh build-dataset
```

This creates a dataset with all 20+ test scenarios from `test-automation/scenarios/`.

### 3. Deploy Kubently

Ensure Kubently is running and accessible:

```bash
# Deploy to Kind cluster
cd ..
./deploy-test.sh

# Port-forward the API
kubectl port-forward -n kubently svc/kubently-api 8080:8080 &
```

### 4. Run Experiments

```bash
# Run with default configurations
./run_experiment.sh run

# Run comparison with multiple prompt variations
./run_experiment.sh compare

# View results
./run_experiment.sh results
```

## Integrated Mode Usage

Use the integrated runner when you want automatic scenario management:

```bash
# Quick test with 3 easy scenarios
./run_integrated.sh quick-test

# Test a single scenario
./run_integrated.sh test-single kubently-scenarios 01-imagepullbackoff-typo

# Test specific scenarios in batch
./run_integrated.sh test-batch kubently-scenarios "01-imagepullbackoff-typo 03-crashloopbackoff"

# Compare different models
./run_integrated.sh compare-models

# Clean up any leftover namespaces
./run_integrated.sh clean
```

## Architecture

### Components

1. **Dataset Builder** (`dataset_builder.py`)
   - Parses existing test scenarios
   - Extracts queries, expected fixes, and metadata
   - Creates LangSmith dataset with inputs/outputs
   - Exports to JSON for backup

2. **Experiment Runner** (`experiment_runner.py`)
   - Manages experiment configurations
   - Executes tests against Kubently API
   - Evaluates responses using custom evaluators
   - Tracks metrics and generates reports

3. **Evaluator** (in `experiment_runner.py`)
   - Scores root cause identification
   - Validates tool usage
   - Measures fix accuracy
   - Assesses response quality

## Dataset Structure

Each example in the dataset contains:

### Inputs
```json
{
  "query": "The user's question about the Kubernetes issue",
  "namespace": "The namespace where the issue exists",
  "cluster_type": "kind",
  "metadata": {
    "scenario_name": "01-imagepullbackoff-typo",
    "scenario_type": "image_pull",
    "difficulty": "easy"
  }
}
```

### Outputs (Expected)
```json
{
  "expected_fix": "The correct solution to the problem",
  "required_tools": ["debug_resource", "get_pod_logs", "execute_kubectl"],
  "validation_checks": ["kubectl commands to verify the fix"],
  "success_criteria": {
    "root_cause_identified": true,
    "fix_proposed": true,
    "tools_used_correctly": true
  }
}
```

## Experiment Configuration

Create custom experiment configurations by modifying prompt templates and model settings:

```python
config = ExperimentConfig(
    prompt_template="Your custom prompt with {query} and {namespace}",
    model_name="gemini-1.5-flash",
    model_provider="gemini",
    temperature=0.3,
    max_tokens=4096,
    metadata={"variant": "custom"}
)
```

### Available Model Providers

- **Gemini**: `gemini-1.5-flash`, `gemini-1.5-pro`
- **Anthropic**: `claude-3-5-sonnet`, `claude-3-5-haiku`
- **OpenAI**: `gpt-4o`, `gpt-4o-mini`

## Evaluation Metrics

The evaluator scores responses on four dimensions:

1. **Root Cause Identification** (30% weight)
   - How well the actual root cause is identified
   - Matches against expected fix keywords

2. **Tool Usage** (20% weight)
   - Whether required tools were used
   - Efficiency of tool selection

3. **Fix Accuracy** (30% weight)
   - Correctness of proposed solution
   - Presence of specific kubectl commands

4. **Response Quality** (20% weight)
   - Clarity and completeness
   - Technical accuracy
   - Appropriate detail level

Overall success threshold: 80%

## Advanced Usage

### Custom Prompt Experiments

Create a JSON configuration file:

```json
[
  {
    "prompt_template": "Your custom prompt...",
    "model_name": "gemini-1.5-flash",
    "model_provider": "gemini",
    "temperature": 0.3,
    "metadata": {"variant": "custom_v1"}
  }
]
```

Run with custom configs:

```bash
python experiment_runner.py \
  --dataset kubently-scenarios \
  --configs my_configs.json \
  --experiment-prefix custom-test
```

### Programmatic Usage

```python
from dataset_builder import DatasetBuilder
from experiment_runner import ExperimentRunner, ExperimentConfig

# Build dataset
builder = DatasetBuilder()
dataset = builder.build_full_dataset("my-dataset")

# Create configurations
configs = [
    ExperimentConfig(
        prompt_template="...",
        model_name="gemini-1.5-flash",
        model_provider="gemini"
    )
]

# Run experiments
runner = ExperimentRunner()
results = await runner.run_experiment(
    dataset_name="my-dataset",
    configs=configs
)
```

### Analyzing Results

Results are saved as JSON files with detailed metrics:

```python
import json

with open("experiment_results_20240327_143022.json") as f:
    results = json.load(f)

for result in results:
    print(f"Experiment: {result['experiment_name']}")
    print(f"Config: {result['config']['metadata']['variant']}")
    print(f"Success Rate: {result['results']['success_rate']}%")
    print(f"Average Score: {result['results']['avg_score']}")
```

## Comparison with Previous Approach

### Before (test-automation)
- Manual test execution with bash scripts
- Results analyzed post-hoc with Gemini
- Limited ability to compare approaches
- No systematic prompt testing

### After (LangSmith)
- Automated dataset management
- Parallel experiment execution
- Real-time evaluation and scoring
- Easy comparison across configurations
- Built-in experiment tracking and versioning

## Viewing Results in LangSmith

1. Go to https://smith.langchain.com/
2. Navigate to your datasets to see test scenarios
3. View experiments to compare different configurations
4. Analyze individual runs for detailed traces
5. Export results for further analysis

## Troubleshooting

### Common Issues

1. **Dataset not found**
   ```bash
   # Rebuild the dataset
   ./run_experiment.sh build-dataset
   ```

2. **API connection failed**
   ```bash
   # Check Kubently is running
   kubectl get pods -n kubently

   # Restart port-forward
   kubectl port-forward -n kubently svc/kubently-api 8080:8080 &
   ```

3. **Missing API keys**
   ```bash
   # Check environment
   env | grep API_KEY

   # Set required keys
   export LANGSMITH_API_KEY=...
   export GOOGLE_API_KEY=...
   ```

## Next Steps

1. **Expand test coverage**: Add more scenarios to the dataset
2. **Custom evaluators**: Create specialized evaluators for specific issue types
3. **Tool optimization**: Test different tool configurations
4. **Prompt engineering**: Systematically test prompt variations
5. **Model comparison**: Benchmark different LLM providers
6. **CI/CD integration**: Automate experiments in CI pipeline

## Resources

- [LangSmith Documentation](https://docs.smith.langchain.com/)
- [LangChain Evaluation Guide](https://python.langchain.com/docs/guides/evaluation)
- [Kubently Test Scenarios](../test-automation/scenarios/)
- [Original Test Automation](../test-automation/README.md)