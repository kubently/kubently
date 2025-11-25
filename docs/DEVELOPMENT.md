# Kubently Development Guide

## Table of Contents
- [Getting Started](#getting-started)
- [Development Environment](#development-environment)
- [Code Structure](#code-structure)
- [Development Workflow](#development-workflow)
- [Testing](#testing)
- [Code Style](#code-style)
- [Contributing](#contributing)
- [Release Process](#release-process)

## Getting Started

### Prerequisites

- Python 3.13+
- Docker 20.10+
- Kubernetes cluster (kind/minikube for local development)
- Redis 7.0+
- Git

### Quick Setup

```bash
# Clone the repository
git clone https://github.com/kubently/kubently.git
cd kubently

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Run initial tests
pytest

# Start local development
docker-compose up -d
```

## Development Environment

### Local Kubernetes Setup

#### Using kind (Recommended)

```bash
# Install kind
brew install kind  # macOS
# or
curl -Lo ./kind https://kind.sigs.k8s.io/dl/latest/kind-linux-amd64
chmod +x ./kind
sudo mv ./kind /usr/local/bin/

# Create cluster
cat <<EOF | kind create cluster --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
  kubeadmConfigPatches:
  - |
    kind: InitConfiguration
    nodeRegistration:
      kubeletExtraArgs:
        node-labels: "ingress-ready=true"
  extraPortMappings:
  - containerPort: 80
    hostPort: 80
    protocol: TCP
  - containerPort: 443
    hostPort: 443
    protocol: TCP
EOF

# Load local images
docker build -t kubently/api:dev ./kubently/api
docker build -t kubently/executor:dev ./kubently/modules/executor
kind load docker-image kubently/api:dev
kind load docker-image kubently/executor:dev
```

#### Using minikube

```bash
# Install minikube
brew install minikube  # macOS

# Start cluster
minikube start --cpus=4 --memory=8192

# Use local Docker daemon
eval $(minikube docker-env)

# Build images directly in minikube
docker build -t kubently/api:dev ./kubently/api
docker build -t kubently/executor:dev ./kubently/modules/executor
```

### Docker Compose Setup

```yaml
# docker-compose.yml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data

  api:
    build:
      context: ./kubently/api
      dockerfile: Dockerfile.dev
    ports:
      - "8000:8000"
    environment:
      - KUBENTLY_REDIS_URL=redis://redis:6379
      - KUBENTLY_API_KEYS=dev-key-1,dev-key-2
      - KUBENTLY_AGENT_TOKENS={"local":"dev-token"}
      - LOG_LEVEL=DEBUG
    depends_on:
      - redis
    volumes:
      - ./kubently/api:/app
    command: uvicorn main:app --reload --host 0.0.0.0 --port 8000

  executor:
    build:
      context: ./kubently/modules/executor
      dockerfile: Dockerfile.dev
    environment:
      - KUBENTLY_API_URL=http://api:8000
      - CLUSTER_ID=local
      - KUBENTLY_TOKEN=dev-token
      - LOG_LEVEL=DEBUG
    depends_on:
      - api
    volumes:
      - ./kubently/modules/executor:/app
      - ~/.kube:/root/.kube:ro
    command: python -u sse_executor.py

volumes:
  redis-data:
```

### IDE Configuration

#### VS Code

```json
// .vscode/settings.json
{
  "python.linting.enabled": true,
  "python.linting.pylintEnabled": false,
  "python.linting.flake8Enabled": true,
  "python.linting.mypyEnabled": true,
  "python.formatting.provider": "black",
  "python.testing.pytestEnabled": true,
  "python.testing.unittestEnabled": false,
  "editor.formatOnSave": true,
  "editor.codeActionsOnSave": {
    "source.organizeImports": true
  }
}
```

```json
// .vscode/launch.json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Debug API",
      "type": "python",
      "request": "launch",
      "module": "uvicorn",
      "args": ["kubently.api.main:app", "--reload", "--port", "8000"],
      "env": {
        "KUBENTLY_REDIS_URL": "redis://localhost:6379",
        "LOG_LEVEL": "DEBUG"
      }
    },
    {
      "name": "Debug Executor",
      "type": "python",
      "request": "launch",
      "program": "${workspaceFolder}/kubently/modules/executor/sse_executor.py",
      "env": {
        "KUBENTLY_API_URL": "http://localhost:8000",
        "CLUSTER_ID": "local",
        "KUBENTLY_TOKEN": "dev-token"
      }
    }
  ]
}
```

#### PyCharm

1. Configure Python interpreter:
   - File → Settings → Project → Python Interpreter
   - Add interpreter → Virtualenv Environment

2. Configure run configurations:
   - Run → Edit Configurations
   - Add New Configuration → Python
   - Script: `kubently/api/main.py` or `kubently/modules/executor/sse_executor.py`
   - Environment variables as needed

## Code Structure

### Module Organization

```
kubently/
├── kubently/
│   ├── api/                    # API service
│   │   ├── __init__.py
│   │   ├── main.py            # FastAPI application
│   │   ├── models.py          # Pydantic models
│   │   ├── auth.py            # Authentication module
│   │   ├── session.py         # Session management
│   │   ├── queue.py           # Queue operations
│   │   └── routes/            # API route handlers
│   │       ├── __init__.py
│   │       ├── debug.py       # Debug endpoints
│   │       ├── agent.py       # Agent endpoints
│   │       └── health.py      # Health checks
│   ├── agent/                 # Agent implementation
│   │   ├── __init__.py
│   │   ├── agent.py          # Main agent logic
│   │   ├── executor.py       # Command execution
│   │   └── validator.py      # Command validation
│   ├── common/                # Shared utilities
│   │   ├── __init__.py
│   │   ├── redis_client.py   # Redis wrapper
│   │   ├── metrics.py        # Metrics collection
│   │   └── logging.py        # Logging configuration
│   └── tests/                 # Test suite
│       ├── unit/              # Unit tests
│       ├── integration/       # Integration tests
│       └── e2e/              # End-to-end tests
├── deployment/                # Kubernetes manifests
├── docs/                      # Documentation
├── scripts/                   # Utility scripts
└── tools/                     # Development tools
```

### Import Guidelines

```python
# Standard library imports
import os
import sys
from datetime import datetime
from typing import Optional, List

# Third-party imports
import redis
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Local imports (absolute imports preferred)
from kubently.api.models import Command, Session
from kubently.api.auth import verify_api_key
from kubently.common.logging import get_logger
```

## Development Workflow

### Git Workflow

```bash
# 1. Create feature branch
git checkout -b feature/your-feature-name

# 2. Make changes and test
# ... edit files ...
pytest tests/unit/test_your_feature.py

# 3. Commit with conventional commits
git add .
git commit -m "feat(api): add session expiration handling"

# 4. Push and create PR
git push origin feature/your-feature-name
```

### Commit Message Convention

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <subject>

<body>

<footer>
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `style`: Code style changes
- `refactor`: Code refactoring
- `test`: Test changes
- `chore`: Build/tooling changes

Examples:
```bash
feat(api): add webhook support for async operations
fix(executor): handle kubectl timeout correctly
docs: update API reference for v2 endpoints
test(session): add expiration edge cases
```

### Pull Request Process

1. **Create PR with template:**

```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] Manual testing completed

## Checklist
- [ ] Code follows style guidelines
- [ ] Self-review completed
- [ ] Documentation updated
- [ ] Tests added/updated
- [ ] No security vulnerabilities
```

2. **Review process:**
   - Automated checks must pass
   - At least one approval required
   - Address review comments
   - Squash and merge when approved

## Testing

### Test Structure

```python
# tests/unit/test_session.py
import pytest
from datetime import datetime, timedelta
from kubently.api.session import SessionManager
from kubently.api.models import Session

class TestSessionManager:
    @pytest.fixture
    def session_manager(self, mock_redis):
        """Create session manager with mock Redis."""
        return SessionManager(redis_client=mock_redis)

    def test_create_session(self, session_manager):
        """Test session creation."""
        session = session_manager.create(
            cluster_id="test-cluster",
            user_id="user-123"
        )

        assert session.cluster_id == "test-cluster"
        assert session.user_id == "user-123"
        assert session.status == "active"

    def test_session_expiration(self, session_manager):
        """Test session expiration logic."""
        session = session_manager.create(
            cluster_id="test-cluster",
            ttl_seconds=1
        )

        # Simulate time passing
        time.sleep(2)

        assert session_manager.is_expired(session.session_id)
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=kubently --cov-report=html

# Run specific test file
pytest tests/unit/test_session.py

# Run with verbose output
pytest -v

# Run integration tests only
pytest tests/integration/

# Run with markers
pytest -m "not slow"

# Watch mode (requires pytest-watch)
ptw
```

### Test Fixtures

```python
# tests/conftest.py
import pytest
import redis
from unittest.mock import MagicMock, patch

@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    mock = MagicMock(spec=redis.Redis)
    mock.get.return_value = None
    mock.set.return_value = True
    return mock

@pytest.fixture
def api_client():
    """Test client for API."""
    from fastapi.testclient import TestClient
    from kubently.api.main import app

    return TestClient(app)

@pytest.fixture
def sample_command():
    """Sample command for testing."""
    return {
        "cluster_id": "test-cluster",
        "command_type": "get",
        "args": ["pods"],
        "namespace": "default"
    }
```

### Integration Testing

```python
# tests/integration/test_api_integration.py
import pytest
import docker
import time

@pytest.mark.integration
class TestAPIIntegration:
    @classmethod
    def setup_class(cls):
        """Start Redis container for tests."""
        cls.client = docker.from_env()
        cls.redis = cls.client.containers.run(
            "redis:7-alpine",
            ports={'6379/tcp': 6379},
            detach=True
        )
        time.sleep(2)  # Wait for Redis to start

    @classmethod
    def teardown_class(cls):
        """Stop and remove Redis container."""
        cls.redis.stop()
        cls.redis.remove()

    def test_full_command_flow(self, api_client):
        """Test complete command execution flow."""
        # Create session
        response = api_client.post("/debug/session", json={
            "cluster_id": "test-cluster"
        })
        assert response.status_code == 201
        session = response.json()

        # Execute command
        response = api_client.post("/debug/execute", json={
            "cluster_id": "test-cluster",
            "session_id": session["session_id"],
            "command_type": "get",
            "args": ["pods"]
        })
        assert response.status_code == 200
```

### Performance Testing

```python
# tests/performance/test_load.py
import asyncio
import aiohttp
import time

async def execute_command(session, api_url, command):
    """Execute single command."""
    async with session.post(f"{api_url}/debug/execute", json=command) as resp:
        return await resp.json()

async def load_test(api_url, num_requests=100):
    """Run load test."""
    async with aiohttp.ClientSession() as session:
        start = time.time()

        tasks = []
        for i in range(num_requests):
            command = {
                "cluster_id": "test-cluster",
                "command_type": "get",
                "args": ["pods"]
            }
            tasks.append(execute_command(session, api_url, command))

        results = await asyncio.gather(*tasks)

        duration = time.time() - start
        print(f"Completed {num_requests} requests in {duration:.2f}s")
        print(f"RPS: {num_requests/duration:.2f}")

if __name__ == "__main__":
    asyncio.run(load_test("http://localhost:8000", 1000))
```

## Code Style

### Python Style Guide

Follow [PEP 8](https://www.python.org/dev/peps/pep-0008/) with these additions:

```python
# Maximum line length: 88 (Black default)
# Use Black for formatting
# Use isort for import sorting

# Good: Clear variable names
session_manager = SessionManager()
command_result = executor.run(command)

# Bad: Unclear abbreviations
sm = SessionManager()
res = executor.run(cmd)

# Good: Type hints
def create_session(
    cluster_id: str,
    user_id: Optional[str] = None,
    ttl: int = 300
) -> Session:
    pass

# Good: Docstrings
def validate_command(args: List[str]) -> bool:
    """
    Validate kubectl command arguments.

    Args:
        args: List of kubectl arguments

    Returns:
        True if command is safe to execute

    Raises:
        ValueError: If command contains forbidden operations
    """
    pass
```

### Linting Configuration

```ini
# .flake8
[flake8]
max-line-length = 88
extend-ignore = E203, W503
exclude = .git,__pycache__,venv,build,dist

# pyproject.toml
[tool.black]
line-length = 88
target-version = ['py313']

[tool.isort]
profile = "black"
line_length = 88

[tool.mypy]
python_version = "3.13"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
```

### Pre-commit Hooks

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/psf/black
    rev: 23.1.0
    hooks:
      - id: black
        language_version: python3.13

  - repo: https://github.com/pycqa/isort
    rev: 5.12.0
    hooks:
      - id: isort

  - repo: https://github.com/pycqa/flake8
    rev: 6.0.0
    hooks:
      - id: flake8

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.0.0
    hooks:
      - id: mypy
        additional_dependencies: [types-redis]
```

Install pre-commit:
```bash
pip install pre-commit
pre-commit install
```

## Contributing

### Setting Up Development Environment

```bash
# Fork and clone
git clone https://github.com/your-username/kubently.git
cd kubently

# Add upstream
git remote add upstream https://github.com/original-org/kubently.git

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install in development mode
pip install -e .
pip install -r requirements-dev.txt

# Install pre-commit hooks
pre-commit install
```

### Development Dependencies

```txt
# requirements-dev.txt
pytest==7.4.0
pytest-cov==4.1.0
pytest-asyncio==0.21.0
pytest-mock==3.13.0
black==23.3.0
flake8==6.0.0
mypy==1.3.0
isort==5.12.0
pre-commit==3.3.0
httpx==0.24.0  # For testing FastAPI
docker==6.1.0  # For integration tests
locust==2.15.0  # For load testing
```

### Module Development Guidelines

When developing a new module:

1. **Follow the black-box principle:**
   - Define clear public interface
   - Hide implementation details
   - Document interface contract

2. **Use dependency injection:**
```python
class QueueManager:
    def __init__(self, redis_client: redis.Redis):
        self._redis = redis_client

    def push_command(self, command: Command) -> str:
        """Push command to queue (public interface)."""
        return self._internal_push(command)

    def _internal_push(self, command: Command) -> str:
        """Internal implementation (hidden)."""
        # Implementation details
        pass
```

3. **Write tests first (TDD):**
```python
# Write test
def test_queue_push_command():
    queue = QueueManager(mock_redis)
    command_id = queue.push_command(sample_command)
    assert command_id is not None

# Then implement
def push_command(self, command: Command) -> str:
    # Implementation to make test pass
    pass
```

## Release Process

### Version Management

Use [Semantic Versioning](https://semver.org/):
- MAJOR.MINOR.PATCH (e.g., 1.2.3)
- MAJOR: Breaking changes
- MINOR: New features (backward compatible)
- PATCH: Bug fixes

### Release Checklist

```bash
# 1. Update version
# Edit kubently/__init__.py
__version__ = "1.2.0"

# 2. Update CHANGELOG.md
# Add release notes

# 3. Run full test suite
pytest
pytest tests/integration/

# 4. Build and test Docker images
docker build -t kubently/api:1.2.0 ./kubently/api
docker build -t kubently/executor:1.2.0 ./kubently/modules/executor

# 5. Create git tag
git tag -a v1.2.0 -m "Release v1.2.0"
git push origin v1.2.0

# 6. Build and push images
docker push kubently/api:1.2.0
docker push kubently/executor:1.2.0

# 7. Update Helm chart
# Update Chart.yaml version and appVersion

# 8. Create GitHub release
# Use tag v1.2.0
# Add release notes from CHANGELOG.md
```

### Hotfix Process

```bash
# 1. Create hotfix branch from tag
git checkout -b hotfix/1.2.1 v1.2.0

# 2. Apply fix and test
# ... make changes ...
pytest

# 3. Update version to 1.2.1

# 4. Merge to main and tag
git checkout main
git merge hotfix/1.2.1
git tag -a v1.2.1 -m "Hotfix v1.2.1"

# 5. Cherry-pick to develop if needed
git checkout develop
git cherry-pick <commit-hash>
```

## Debugging Tips

### Common Issues

#### Redis Connection Issues
```python
# Add debug logging
import logging
logging.basicConfig(level=logging.DEBUG)

# Test Redis connection
import redis
r = redis.Redis(host='localhost', port=6379)
r.ping()  # Should return True
```

#### Executor Not Receiving Commands
```python
# Check SSE connections
kubectl logs -l app=kubently-api | grep "SSE"

# Check Redis pub/sub
r = redis.Redis()
r.pubsub_channels("executor-commands:*")
```

#### Session Expiration
```python
# Check session in Redis
r.get("session:sess-abc-123")
r.ttl("session:sess-abc-123")  # Check TTL
```

### Debug Tools

```bash
# Redis CLI monitoring
redis-cli monitor

# API debug mode
LOG_LEVEL=DEBUG uvicorn kubently.api.main:app --reload

# Executor verbose mode
LOG_LEVEL=DEBUG python kubently/modules/executor/sse_executor.py

# Network debugging
tcpdump -i any -w kubently.pcap host api.kubently.example.com

# Kubernetes debugging
kubectl logs -f deployment/kubently-executor -n kubently
kubectl exec -it deployment/kubently-api -- /bin/sh
```

## Performance Optimization

### Profiling

```python
# Using cProfile
import cProfile
import pstats

def profile_command_execution():
    profiler = cProfile.Profile()
    profiler.enable()

    # Code to profile
    execute_command(command)

    profiler.disable()
    stats = pstats.Stats(profiler)
    stats.sort_stats('cumulative')
    stats.print_stats(10)

# Using memory_profiler
from memory_profiler import profile

@profile
def memory_intensive_function():
    # Function to analyze
    pass
```

### Optimization Guidelines

1. **Use connection pooling:**
```python
redis_pool = redis.ConnectionPool(
    host='localhost',
    port=6379,
    max_connections=50
)
redis_client = redis.Redis(connection_pool=redis_pool)
```

2. **Implement caching:**
```python
from functools import lru_cache

@lru_cache(maxsize=128)
def get_cluster_config(cluster_id: str):
    # Expensive operation
    return fetch_from_database(cluster_id)
```

3. **Use async where appropriate:**
```python
async def execute_commands_parallel(commands: List[Command]):
    tasks = [execute_command(cmd) for cmd in commands]
    return await asyncio.gather(*tasks)
```

## Security Guidelines

### Code Security

1. **Never log sensitive data:**
```python
# Bad
logger.info(f"Token: {token}")

# Good
logger.info(f"Token: {token[:8]}...")
```

2. **Validate all inputs:**
```python
def validate_cluster_id(cluster_id: str) -> str:
    if not re.match(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$', cluster_id):
        raise ValueError("Invalid cluster ID format")
    return cluster_id
```

3. **Use secrets properly:**
```python
# Never hardcode secrets
API_KEY = os.environ.get('API_KEY')
if not API_KEY:
    raise ValueError("API_KEY environment variable not set")
```

### Security Testing

```bash
# Run security scan
pip install bandit
bandit -r kubently/

# Check dependencies
pip install safety
safety check

# SAST scanning
docker run --rm -v $(pwd):/src \
  returntocorp/semgrep:latest \
  --config=auto /src
```

## Resources

### Documentation
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Pydantic Documentation](https://pydantic-docs.helpmanual.io/)
- [Redis Documentation](https://redis.io/documentation)
- [Kubernetes API Reference](https://kubernetes.io/docs/reference/)

### Tools
- [Black Formatter](https://black.readthedocs.io/)
- [pytest](https://docs.pytest.org/)
- [Docker](https://docs.docker.com/)
- [kind](https://kind.sigs.k8s.io/)

### Community
- GitHub Issues: Report bugs and request features
- Discussions: Ask questions and share ideas
- Slack: Join #kubently channel (invite link in README)
