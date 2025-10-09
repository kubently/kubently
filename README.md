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
- **Natural Language Interface**: Conversational Kubernetes troubleshooting and debugging
- **Comprehensive Analysis**: Automated issue detection, root cause analysis, and solution recommendations
- **Security-First**: API key authentication, OAuth/OIDC support, and TLS with cert-manager
- **Persistent Sessions**: Redis-backed conversation history and context management
- **Extensive Tool Suite**: kubectl integration, log analysis, resource inspection, and more

## Quick Start

### For Users: Get Started in 5 Minutes

```bash
# Install CLI
npm install -g @kubently/cli

# Deploy to your cluster
git clone https://github.com/your-org/kubently.git
cd kubently
kubectl create namespace kubently

# Create secrets (LLM API key + admin key)
./secrets/generate-redis-password.sh
kubectl create secret generic kubently-llm-secrets -n kubently \
  --from-literal=ANTHROPIC_API_KEY="your-key"
export ADMIN_KEY=$(openssl rand -hex 32)
kubectl create secret generic kubently-api-keys -n kubently \
  --from-literal=keys="admin:${ADMIN_KEY}"

# Deploy with Helm
helm install kubently ./deployment/helm/kubently \
  --namespace kubently \
  -f deployment/helm/quick-start-values.yaml

# Configure CLI
kubently init
# API URL: http://localhost:8080 (or your ingress URL)
# API Key: <your admin key>

# Start troubleshooting
kubently debug
```

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

MIT License - See LICENSE file for details
