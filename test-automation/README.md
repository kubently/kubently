# Kubently Test Automation

Comprehensive test automation system for Kubently with AI-powered analysis and actionable insights.

## Overview

This test automation framework provides:
- **Full Data Capture**: Tool calls, responses, timing, and performance metrics
- **AI Analysis**: Google Gemini-powered insights and recommendations
- **Interactive CLI**: User-friendly interface with scenario selection
- **Detailed Reports**: JSON, Markdown, and structured output formats

## Quick Start

```bash
# Interactive mode (recommended) - shows scenario selector
./run_tests.sh --api-key test-api-key

# Test all scenarios with analysis
export GOOGLE_API_KEY=your-gemini-api-key
./run_tests.sh test-and-analyze --api-key test-api-key

# Test specific scenario
./run_tests.sh test-and-analyze --api-key test-api-key --scenario 14-service-port-mismatch

# Test without analysis (no Gemini required)
./run_tests.sh test-only --api-key test-api-key
```

## Prerequisites

1. **Kubently Deployment**
   ```bash
   # Deploy Kubently (from repo root)
   ./deploy-test.sh
   
   # Or ensure port-forward is active
   kubectl port-forward -n kubently svc/kubently-api 8080:8080
   ```

2. **Environment Variables**
   ```bash
   # Required for AI analysis
   export GOOGLE_API_KEY=your-gemini-api-key
   
   # Optional overrides
   export KUBENTLY_API_URL=http://localhost:8080
   ```

## Commands

### `test-and-analyze` (default)
Runs tests and performs AI analysis.

```bash
./run_tests.sh test-and-analyze --api-key KEY [--scenario NAME]
```

**Options:**
- `--api-key KEY` - API key for Kubently (required)
- `--api-url URL` - Override API URL (default: http://localhost:8080)
- `--scenario NAME` - Run specific scenario only

### `test-only`
Runs tests without analysis (no Gemini API required).

```bash
./run_tests.sh test-only --api-key KEY [--scenario NAME]
```

### `analyze-only`
Analyzes existing test results.

```bash
# Interactive result selection
./run_tests.sh analyze-only

# Analyze specific results
./run_tests.sh analyze-only --scenario test-results-20250913_135632
```

### `interactive`
Shows interactive menu for scenario selection.

```bash
./run_tests.sh interactive --api-key KEY
```

## Test Scenarios

All scenarios are in `scenarios/`:

| Scenario | Description | Issue Type |
|----------|-------------|------------|
| 01-imagepullbackoff-typo | Image name typo | Container startup |
| 02-imagepullbackoff-private | Missing registry credentials | Authentication |
| 03-crashloopbackoff | Application crash | Application error |
| 04-runcontainer-missing-configmap | Missing ConfigMap | Configuration |
| 05-runcontainer-missing-secret-key | Missing Secret key | Configuration |
| 06-oomkilled | Out of memory | Resources |
| 07-failed-readiness-probe | Readiness probe failure | Health checks |
| 08-failing-liveness-probe | Liveness probe failure | Health checks |
| 09-mismatched-labels | Service selector mismatch | Networking |
| 10-unschedulable-resources | Insufficient resources | Scheduling |
| 11-unschedulable-taint | Node taint issues | Scheduling |
| 12-pvc-unbound | PVC binding issues | Storage |
| 13-service-selector-mismatch | Service routing | Networking |
| 14-service-port-mismatch | Port configuration | Networking |
| 15-network-policy-deny-ingress | Network policy ingress | Security |
| 16-network-policy-deny-egress | Network policy egress | Security |
| 17-cross-namespace-block | Cross-namespace access | Security |
| 18-missing-serviceaccount | ServiceAccount missing | RBAC |
| 19-rbac-forbidden-role | RBAC Role issues | RBAC |
| 20-rbac-forbidden-clusterrole | RBAC ClusterRole issues | RBAC |

## Output Structure

Each test run creates a timestamped directory:

```
test-results-20250913_135632/
├── 14-service-port-mismatch.json    # Test result with:
│                                     # - Tool calls (actual kubectl commands)
│                                     # - Agent responses
│                                     # - Timing and performance metrics
├── traces/                          # Detailed execution traces
│   └── 14-service-port-mismatch_trace.json
├── analysis/                        # AI-powered analysis (if enabled)
│   ├── report.md                   # Human-readable recommendations
│   └── report.json                 # Structured analysis data
└── summary.json                    # Overall test summary
```

## Key Features

### Tool Call Exposure
The system now captures actual tool calls made by the agent:
- Tool name, parameters, and results
- Timing information
- Success/failure status

Example captured tool call:
```json
{
  "tool_name": "execute_kubectl",
  "parameters": {
    "cluster_id": "kind",
    "command": "describe",
    "resource": "service/web-service",
    "namespace": "test-scenario-14"
  },
  "output": "Name: web-service\nNamespace: test-scenario-14\n...",
  "success": true
}
```

### AI Analysis

Gemini analyzer provides:
- **Root Cause Analysis**: Did the agent find the issue? (confidence score)
- **Efficiency Analysis**: Tool usage optimization opportunities
- **Quality Assessment**: Accuracy, completeness, clarity scores
- **Actionable Recommendations**: Specific improvements with examples

### Success Metrics

- **Success Rate**: % of scenarios correctly diagnosed
- **Tool Efficiency**: Optimal vs actual tool calls
- **Response Time**: Duration of diagnosis
- **Root Cause Accuracy**: Whether the core issue was identified

## Direct Python Usage

For advanced users or CI/CD integration:

```bash
# With virtual environment (recommended)
source venv/bin/activate

# Run specific scenario
python test_runner.py \
  --api-url http://localhost:8080 \
  --api-key test-api-key \
  --scenario 14-service-port-mismatch

# Skip analysis
python test_runner.py \
  --api-key test-api-key \
  --skip-analysis

# Analyze existing results
python analyzer.py \
  --result-file test-results-20250913_135632/14-service-port-mismatch.json \
  --output analysis.md
```

## Virtual Environment

The `run_tests.sh` script automatically manages a Python virtual environment:
- Creates `venv` if it doesn't exist
- Installs all dependencies
- Activates/deactivates as needed

Manual setup:
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Troubleshooting

### "Kubently is not running"
```bash
# Check deployment
kubectl get pods -n kubently

# Restart port-forward
kubectl port-forward -n kubently svc/kubently-api 8080:8080
```

### "GOOGLE_API_KEY not set"
```bash
# For analysis features
export GOOGLE_API_KEY=your-gemini-api-key

# Or run without analysis
./run_tests.sh test-only --api-key test-api-key
```

### "No module named 'httpx'"
```bash
# Script auto-installs, but for manual fix:
pip install httpx rich google-generativeai
```

### Test hangs or times out
- Check Kubently logs: `kubectl logs -n kubently -l app.kubernetes.io/component=api`
- Ensure executor is running: `kubectl get pods -n kubently | grep executor`
- Verify cluster access in scenarios

## Development

### Adding New Scenarios

1. Create script in `scenarios/`:
   ```bash
   #!/bin/bash
   # Expected fix: Clear description of the solution
   
   setup() {
       kubectl create namespace test-ns-21
       # Create resources that have issues
   }
   
   cleanup() {
       kubectl delete namespace test-ns-21
   }
   
   case "$1" in
       setup) setup ;;
       cleanup) cleanup ;;
   esac
   ```

2. Test individually:
   ```bash
   ./run_tests.sh test-only --api-key KEY --scenario 21-new-scenario
   ```

### Customizing Analysis

Edit `analyzer.py` to modify:
- Analysis prompts in `analyze_test_result()`
- Scoring algorithms
- Report format in `generate_markdown_report()`

### Architecture

1. **test_runner.py**: Main execution engine
   - Manages scenario lifecycle (setup/test/cleanup)
   - Captures SSE stream from A2A protocol
   - Parses tool calls from status events
   - Saves structured results

2. **analyzer.py**: AI-powered analysis
   - Uses Gemini 2.0 Flash (thinking model)
   - Analyzes tool usage patterns
   - Generates actionable recommendations

3. **run_tests.sh**: User-friendly CLI
   - Virtual environment management
   - Interactive menus
   - Batch operations

4. **a2a_test_client.py**: Protocol implementation
   - Direct httpx-based A2A client
   - SSE parsing
   - Tool call extraction

## Best Practices

1. **Regular Testing**: Run full suite weekly to track improvements
2. **Scenario Coverage**: Ensure all common K8s issues are covered
3. **Analysis Review**: Act on Gemini recommendations systematically
4. **Baseline Tracking**: Compare results over time

## Health Check Suppression

The system suppresses noisy health check logs via `kubently/logging_config.py` for cleaner output in production environments.