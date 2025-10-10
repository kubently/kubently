# Kubently Test Automation Framework Guide

## Overview
This framework provides automated testing for Kubently's Kubernetes debugging capabilities, with comprehensive data capture and AI-powered analysis using Google's Gemini.

## Directory Structure
```
test-automation/
├── comprehensive_test_runner.py  # Main test runner with full data capture
├── gemini_analyzer.py            # Gemini AI analysis module
├── simple_tester.py              # Simplified quick tester
├── run_tests.sh                  # Wrapper script with venv activation
├── setup.sh                      # Setup script for dependencies
├── requirements.txt              # Python dependencies
├── scenarios/                    # Test scenario scripts
├── comprehensive-results/        # All test output
│   ├── logs/                    # Kubently response logs
│   ├── traces/                  # Detailed debug traces
│   └── analysis/                # Gemini AI analysis results
├── archive/                      # Deprecated/old files
└── venv/                        # Python virtual environment
```

## Setup

### 1. Install Dependencies
```bash
./setup.sh
```

### 2. Set Environment Variables
```bash
export KUBENTLY_API_KEY="your-kubently-api-key"
export GOOGLE_API_KEY="your-gemini-api-key"  # For AI analysis
export KUBENTLY_API_URL="http://localhost:8080"  # Default
```

## Usage

### Running Tests

#### Comprehensive Test (Recommended)
```bash
# Run all scenarios with full data capture
./run_tests.sh test

# Run specific scenario
./run_tests.sh test 01-imagepullbackoff-typo
```

#### With Manual Python
```bash
source venv/bin/activate
python3 comprehensive_test_runner.py \
    --api-url http://localhost:8080 \
    --api-key YOUR_KEY \
    --scenario 01-imagepullbackoff-typo
```

### Analyzing Results

#### Analyze Previous Test
```bash
# Analyze specific result
./run_tests.sh analyze 01-imagepullbackoff-typo_20250908_084451.json

# Analyze most recent
./run_tests.sh analyze

# Analyze all results
./run_tests.sh analyze all
```

#### Manual Analysis
```bash
source venv/bin/activate
python3 comprehensive_test_runner.py \
    --api-key YOUR_KEY \
    --analyze-previous 01-imagepullbackoff-typo_20250908_084451.json
```

## Test Scenarios

Available scenarios in `scenarios/`:
- `01-imagepullbackoff-typo.sh` - Image name typo
- `02-crashloopbackoff-bad-command.sh` - Invalid command
- `03-oomkilled-memory-limit.sh` - Memory limit exceeded
- `04-service-selector-mismatch.sh` - Service selector issue
- `05-configmap-missing.sh` - Missing ConfigMap
- `06-pvc-not-bound.sh` - PVC binding issue
- `07-nodeport-access.sh` - NodePort access problem
- `08-resource-quota-exceeded.sh` - Quota exceeded
- `09-readiness-probe-failing.sh` - Probe failure
- `10-dns-resolution-failure.sh` - DNS issues

## Data Captured

### 1. Responses
- All Kubently responses
- Streaming data chunks
- Final summaries

### 2. Tool Calls
- Tool name and arguments
- Results
- Timing information

### 3. Thinking Steps
- Internal reasoning
- Decision points
- Analysis steps

### 4. Token Usage
- Input tokens
- Output tokens
- Total usage

### 5. Metrics
- Response times
- Tool efficiency scores
- Success rates

## Gemini Analysis Features

The framework uses Gemini AI to analyze:

### Root Cause Analysis
- Accuracy of root cause identification
- Comparison with expected fixes
- Confidence levels

### Efficiency Metrics
- Tool usage patterns
- Token efficiency
- Response time analysis
- Redundant operations

### Bottlenecks
- Slow operations
- Inefficient tool sequences
- Areas for optimization

### Recommendations
- Prompt improvements
- Tool enhancements
- Workflow optimizations

## Output Files

### Test Results
`comprehensive-results/[scenario]_[timestamp].json`
- Complete test metadata
- Summary statistics
- Quick reference

### Debug Traces
`comprehensive-results/traces/[scenario]_[timestamp]_trace.json`
- Full conversation history
- All tool calls and results
- Thinking steps
- Token usage

### Logs
`comprehensive-results/logs/[scenario]_[timestamp].log`
- Raw response text
- Useful for debugging

### Analysis
`comprehensive-results/analysis/[scenario]_[timestamp]_gemini_analysis.json`
- AI-powered insights
- Efficiency scores
- Improvement recommendations

## Troubleshooting

### ModuleNotFoundError
Run `./setup.sh` to install dependencies

### 404 Not Found
Ensure Kubently is running and accessible at the configured URL

### Gemini API Issues
- Check GOOGLE_API_KEY is set
- Verify API key has access to Gemini 2.0 Flash
- Check quota limits

### Scenario Hanging
Scenarios filter out `kubectl watch` commands automatically. If still hanging, check for other blocking commands.

## Advanced Usage

### Custom Scenarios
Create new scenarios in `scenarios/` following the pattern:
```bash
#!/bin/bash
kubectl create namespace test-scenario
kubectl apply -f - <<EOF
# Your Kubernetes manifests
EOF
```

### Extending Analysis
Modify `gemini_analyzer.py` to add custom analysis prompts or metrics.

### Integration with CI/CD
The framework can be integrated into CI/CD pipelines:
```yaml
- name: Run Kubently Tests
  run: |
    export KUBENTLY_API_KEY=${{ secrets.KUBENTLY_KEY }}
    export GOOGLE_API_KEY=${{ secrets.GEMINI_KEY }}
    ./run_tests.sh comprehensive
```

## Best Practices

1. **Regular Testing**: Run tests after Kubently updates
2. **Review Analysis**: Check Gemini insights for patterns
3. **Update Scenarios**: Add new scenarios for discovered edge cases
4. **Monitor Metrics**: Track efficiency scores over time
5. **Archive Results**: Keep historical data for comparison

## Support

For issues or questions:
- Check test logs in `comprehensive-results/logs/`
- Review traces in `comprehensive-results/traces/`
- Examine analysis in `comprehensive-results/analysis/`