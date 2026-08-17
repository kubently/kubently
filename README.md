# Kubently

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.13%2B-blue.svg)](https://www.python.org/)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-1.28%2B-blue.svg)](https://kubernetes.io/)
[![A2A Protocol](https://img.shields.io/badge/A2A-Protocol-green.svg)](https://a2a-protocol.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue.svg)](https://www.docker.com/)
[![Helm](https://img.shields.io/badge/Helm-Ready-blue.svg)](https://helm.sh/)
[![Security Policy](https://img.shields.io/badge/Security-Policy-yellow.svg)](SECURITY.md)
[![Contributing](https://img.shields.io/badge/Contributing-Welcome-brightgreen.svg)](CONTRIBUTING.md)

Kubently - Troubleshooting Kubernetes Agentically

## Overview

Kubently (*Kubernetes + Agentically*) is a **free, self-hosted, vendor-neutral multi-cluster Kubernetes troubleshooter**. Ask one question, get AI-diagnosed answers from every cluster in your fleet in parallel — including clusters you can't reach directly: executors dial **outbound** to the central API, so there's no inbound ingress, no shared kubeconfig, and no per-cluster credentials to distribute.

Agents collaborate over the [A2A (Agent-to-Agent) protocol](https://a2a-protocol.org/latest/), and any MCP client (Claude Code, Cursor, Claude Desktop) can use Kubently as a tool out of the box.

## Key Features

- **Multi-Cluster Fleet Troubleshooting**: One question fans out across all registered clusters in parallel
- **Outbound-Dial Executors**: Reach clusters behind firewalls/NAT — no inbound ingress, no shared kubeconfig
- **Natural Language Interface**: Conversational Kubernetes troubleshooting and debugging
- **Comprehensive Analysis**: Automated issue detection, root cause analysis, and solution recommendations
- **Multi-LLM Support**: Compatible with Google Gemini, OpenAI, Anthropic, and other providers
- **A2A Protocol**: Industry-standard agent-to-agent communication for complex workflows
- **MCP Server**: Optional [Model Context Protocol](docs/MCP.md) endpoint so MCP clients (Claude Desktop, Cursor, custom agents) get direct tool access
- **Security-First**: API key authentication, OAuth/OIDC support, and TLS with cert-manager
- **Persistent Sessions**: Redis-backed conversation history and context management
- **Extensive Tool Suite**: kubectl integration, log analysis, resource inspection, and more

## Quick Start

### For Users: Get Started in 5 Minutes

Point `kubectl` at any cluster (kind, minikube, or real) and run:

```bash
npm install -g @kubently/cli
kubently install
```

That's it. The CLI installs Kubently via Helm, wires up secrets and the
executor, port-forwards the API, and drops you into a debug chat:

```
kubently> why is my nginx pod crashlooping?
```

You'll need an LLM API key (Anthropic, OpenAI, or Google) — the installer
prompts for it, or reads `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
`GOOGLE_API_KEY` from your environment. Use `--provider` to pick the LLM,
`--chart ./deployment/helm/kubently` to install from a local checkout, and
`kubently install --help` for everything else.

### Use from Claude Code / Cursor (MCP)

Already ran `kubently install`? Add Kubently to Claude Code:

```bash
claude mcp add kubently -- kubently mcp
```

Or connect directly over HTTP (no bridge process):

```bash
claude mcp add --transport http kubently http://localhost:8080/mcp/ \
  --header "X-API-Key: <your-api-key>"
```

Then ask Claude things like *"use kubently to figure out why payments pods are
crashlooping"*. Any MCP client works — see [docs/MCP.md](docs/MCP.md) for
Cursor and generic configuration.

### Proactive diagnosis (Alertmanager → Slack)

Set `api.env.SLACK_WEBHOOK_URL` to a Slack incoming-webhook URL and point
Alertmanager at Kubently:

```yaml
receivers:
  - name: kubently
    webhook_configs:
      - url: https://<your-kubently-host>/webhooks/alertmanager
        http_config:
          http_headers:            # Alertmanager >= 0.28
            X-API-Key:
              secrets: ["<your-api-key>"]
```

Each firing alert is diagnosed by the agent and the result is posted to Slack —
the bot often explains the root cause before you've opened your laptop.

### Scheduled fleet health digest

Alerts are reactive. A digest sweeps *every* registered cluster on a schedule and
posts one summary to the same Slack webhook — healthy clusters collapse to a
single line, so what's left is what needs you.

```yaml
fleetReport:
  enabled: true
  schedule: "0 13 * * 1-5"   # weekday mornings
```

Preview it before you schedule it — `dry_run` returns the digest and posts
nothing:

```bash
curl -X POST https://<your-kubently-host>/webhooks/fleet-report \
  -H "X-API-Key: <your-api-key>" -H 'Content-Type: application/json' \
  -d '{"dry_run": true}'
```

The digest question is yours to change. Pass `query` in that request to try one
immediately, then keep the wording you like via `fleetReport.query` in values:

```yaml
fleetReport:
  query: |-
    Check every cluster for pods restarting more than 5 times and for PVCs above
    85% usage. One line per healthy cluster. No preamble.
```

To run the real scheduled path once — image, secrets, in-cluster URL and all:

```bash
kubectl create job --from=cronjob/kubently-fleet-report fleet-report-test -n kubently
```

**📖 See [QUICK_START.md](docs/QUICK_START.md) for full quick-start guide**

**📚 See [GETTING_STARTED.md](docs/GETTING_STARTED.md) for production deployment**

### For Developers: Local Testing

```bash
# Deploy to a local kind cluster (builds images from HEAD)
ANTHROPIC_API_KEY=sk-... ./deployment/scripts/kind-e2e.sh

# Run comprehensive test suite
./test-automation/run_tests.sh test-and-analyze --api-key test-api-key
```

**📖 See [CLAUDE.md](CLAUDE.md) for development guidelines**

## Configuration

### LLM Providers

Configure your preferred LLM provider in `.env`:

```bash
# Google Gemini
GOOGLE_API_KEY=your-gemini-api-key

# OpenAI
OPENAI_API_KEY=your-openai-api-key

# Anthropic
ANTHROPIC_API_KEY=your-anthropic-api-key
```

### Helm Deployment

Customize deployment using Helm values:

```bash
# Edit deployment configuration
vim deployment/helm/test-values.yaml

# Deploy with custom values
helm install kubently deployment/helm -f deployment/helm/test-values.yaml
```

## Architecture

- **API Server**: FastAPI-based REST API for cluster management and authentication
- **A2A Server**: Implements A2A protocol with LangGraph for workflow orchestration
- **Test Automation**: Comprehensive testing framework with 20+ Kubernetes scenarios
- **CLI Tools**: Modern Node.js CLI for interactive debugging

### Agent Toolset

The diagnostic agent investigates with a small set of read-only tools:

- **`list_clusters`** — enumerate registered clusters
- **`execute_kubectl`** — read-only kubectl against one cluster (whitelist-enforced on the executor)
- **`execute_kubectl_multi`** — one read-only kubectl command fanned out across many clusters
- **`get_recent_changes`** — "what changed?" timeline for a workload or namespace: rollouts (ReplicaSet revisions + change-causes), Helm release history *(opt-in: `changeCorrelation.helmHistory.enabled`)*, ArgoCD sync history *(optional: `changeCorrelation.argocd.url`)*, and Normal+Warning events — correlated against first-error timestamps in the RCA
- **`get_events_for_resource`** — chronological events for a resource and its children (deployment → replicasets → pods)

## Documentation

### Getting Started
- **[Quick Start Guide](docs/QUICK_START.md)** - Get running in 5 minutes
- **[Getting Started](docs/GETTING_STARTED.md)** - Complete setup for production use
- **[Deployment Guide](docs/DEPLOYMENT.md)** - Detailed deployment options and configuration

### Usage & Operations
- **[CLI Admin Guide](docs/GETTING_STARTED.md#step-5-register-and-deploy-executors)** - Managing clusters and executors
- **[Test Queries](docs/TEST_QUERIES.md)** - Example API requests and A2A protocol usage
- **[MCP Connect Guide](docs/MCP.md)** - Connect MCP clients (Claude Desktop, Cursor, custom agents)
- **[Environment Variables](docs/ENVIRONMENT_VARIABLES.md)** - Configuration reference

### Architecture & Development
- **[Architecture](docs/ARCHITECTURE.md)** - System design and components
- **[A2A Protocol Spec](https://a2a-protocol.org/latest/)** - Official protocol documentation
- **[Development Guide](CLAUDE.md)** - Guidelines for contributors

### Troubleshooting
- **[Getting Started - Common Issues](docs/GETTING_STARTED.md#common-issues)** - Troubleshooting guide

## Contributing

See [CLAUDE.md](CLAUDE.md) for development guidelines and contribution instructions.

## Maintainer

**Kubently Team** - [hello@kubently.io](mailto:hello@kubently.io)

## License

Apache 2.0 License - See LICENSE file for details
