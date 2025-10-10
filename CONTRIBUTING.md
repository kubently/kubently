# Contributing to Kubently

First off, thank you for considering contributing to Kubently! It's people like you that make Kubently such a great tool for troubleshooting Kubernetes agentically.

## Project Lead

This project is maintained by the **Kubently Team** ([hello@kubently.io](mailto:hello@kubently.io)).

## Code of Conduct

By participating in this project, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md). Please read it before contributing.

## How Can I Contribute?

### Reporting Bugs

Before creating bug reports, please check existing issues as you might find out that you don't need to create one. When you are creating a bug report, please include as many details as possible:

- **Use a clear and descriptive title** for the issue to identify the problem
- **Describe the exact steps which reproduce the problem** in as many details as possible
- **Provide specific examples to demonstrate the steps**
- **Describe the behavior you observed after following the steps** and point out what exactly is the problem with that behavior
- **Explain which behavior you expected to see instead and why**
- **Include logs** if relevant (use `kubectl logs` or relevant debug output)
- **Include your environment details** (Kubernetes version, cloud provider, etc.)

### Suggesting Enhancements

Enhancement suggestions are tracked as GitHub issues. When creating an enhancement suggestion, please include:

- **Use a clear and descriptive title** for the issue to identify the suggestion
- **Provide a step-by-step description of the suggested enhancement** in as many details as possible
- **Provide specific examples to demonstrate the steps** or point out the part of Kubently where the suggestion is related to
- **Describe the current behavior** and **explain which behavior you expected to see instead** and why
- **Explain why this enhancement would be useful** to most Kubently users

### Pull Requests

1. Fork the repo and create your branch from `main`
2. If you've added code that should be tested, add tests
3. If you've changed APIs, update the documentation
4. Ensure the test suite passes
5. Make sure your code follows the existing code style
6. Issue that pull request!

## Development Setup

### Prerequisites

- Python 3.13+
- Node.js 20+ (for CLI)
- Docker and Docker Compose
- Kubernetes cluster (Kind, Minikube, or cloud provider)
- kubectl configured

### Setting Up Your Development Environment

1. **Clone your fork:**
   ```bash
   git clone https://github.com/your-username/kubently.git
   cd kubently
   ```

2. **Create a virtual environment:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   # Python dependencies
   pip install -e .
   pip install -r requirements-dev.txt

   # Node.js CLI dependencies
   cd kubently-cli/nodejs
   npm install
   npm run build
   cd ../..
   ```

4. **Set up environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

5. **Run locally:**
   ```bash
   # Start Redis
   docker-compose up -d redis

   # Start API server
   make run-local

   # In another terminal, start A2A server
   make run-a2a
   ```

## Testing

### Running Tests

```bash
# Run all tests
make test

# Run Python tests
pytest kubently/tests/

# Run with coverage
pytest --cov=kubently kubently/tests/

# Run linting
make lint

# Run type checking
make typecheck
```

### Writing Tests

- Write unit tests for all new functionality
- Ensure tests are deterministic and don't depend on external services when possible
- Use mocks for external dependencies
- Follow the existing test structure and naming conventions

## Code Style

### Python Code Style

We use `ruff` for Python linting and formatting:

```bash
# Format code
ruff format kubently/

# Check for issues
ruff check kubently/

# Fix auto-fixable issues
ruff check --fix kubently/
```

### TypeScript/JavaScript Code Style

We use `prettier` and `eslint` for Node.js code:

```bash
cd kubently-cli/nodejs
npm run lint
npm run format
```

### Commit Messages

We follow conventional commits specification:

- `feat:` A new feature
- `fix:` A bug fix
- `docs:` Documentation only changes
- `style:` Changes that do not affect the meaning of the code
- `refactor:` A code change that neither fixes a bug nor adds a feature
- `perf:` A code change that improves performance
- `test:` Adding missing tests or correcting existing tests
- `chore:` Changes to the build process or auxiliary tools

Examples:
```
feat: add support for custom kubectl commands
fix: resolve race condition in session management
docs: update API documentation for auth endpoints
```

## Developer Certificate of Origin (DCO)

By contributing to this project, you certify that:

- The contribution was created in whole or in part by you and you have the right to submit it under the Apache 2.0 license
- The contribution is based upon previous work that, to the best of your knowledge, is covered under an appropriate open source license and you have the right under that license to submit that work with modifications
- The contribution was provided directly to you by some other person who certified (a), (b) or (c) and you have not modified it

This is indicated by adding a "Signed-off-by" line to your commit messages:

```
Signed-off-by: Jane Smith <jane.smith@example.com>
```

You can add this automatically by using the `-s` flag:

```bash
git commit -s -m "Your commit message"
```

## Pull Request Process

1. **Update the README.md** with details of changes to the interface, if applicable
2. **Update the CHANGELOG.md** with a note describing your changes
3. **Update documentation** in the `/docs` directory if you're changing functionality
4. **Ensure all tests pass** locally before submitting
5. **Request review** from maintainers
6. **Address feedback** promptly and update your PR as needed

## Project Structure

```
kubently/
├── kubently/              # Main Python package
│   ├── api/              # REST API endpoints
│   ├── modules/          # Core modules (A2A, auth, etc.)
│   ├── tests/            # Python tests
│   └── utils/            # Utility functions
├── kubently-cli/         # CLI tools
│   └── nodejs/           # Node.js CLI implementation
├── deployment/           # Deployment configurations
│   ├── docker/           # Docker configurations
│   ├── helm/             # Helm charts
│   └── kubernetes/       # K8s manifests
├── docs/                 # Documentation
├── test-automation/      # Test automation framework
└── tests/                # Additional tests
```

## Where to Get Help

- **GitHub Issues**: For bug reports and feature requests
- **Discussions**: For general questions and discussions (coming soon)
- **Documentation**: Check `/docs` directory for detailed guides

## Recognition

Contributors will be recognized in our CHANGELOG.md and in the project README. We value all contributions, whether they're bug fixes, features, documentation improvements, or community support.

## Questions?

Feel free to open an issue with the label `question` if you have any questions about contributing.

Thank you for contributing to Kubently! 🚀