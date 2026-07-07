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

Kubently (*Kubernetes + Agentically*) enables AI agents to troubleshoot Kubernetes clusters through natural language conversations. By implementing the [A2A (Agent-to-Agent) protocol](https://a2a-protocol.org/latest/), Kubently allows multiple agents to collaborate in diagnosing and resolving cluster issues autonomously.

## Key Features

- **Multi-LLM Support**: Compatible with Google Gemini, OpenAI, Anthropic, and other providers
- **A2A Protocol**: Industry-standard agent-to-agent communication for complex workflows
- **MCP Server**: Optional [Model Context Protocol](docs/MCP.md) endpoint so MCP clients (Claude Desktop, Cursor, custom agents) get direct tool access
- **Natural Language Interface**: Conversational Kubernetes troubleshooting and debugging
- **Comprehensive Analysis**: Automated issue detection, root cause analysis, and solution recommendations
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

**📖 See [QUICK_START.md](docs/QUICK_START.md) for full quick-start guide**

**📚 See [GETTING_STARTED.md](docs/GETTING_STARTED.md) for production deployment**

### For Developers: Local Testing

```bash
# Deploy with automated testing
./deploy-test.sh

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
