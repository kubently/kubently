# Module: Kubently Agent

## Black Box Interface

**Purpose**: Execute kubectl commands in Kubernetes clusters

**What this module does** (Public Interface):
- Polls API for commands via HTTP
- Executes kubectl commands
- Returns results via HTTP

**What this module hides** (Implementation):
- Polling strategy and intervals
- kubectl execution details
- Error handling and retries
- Result formatting
- Authentication mechanism

## Overview
The Agent module is a completely independent black box that could be rewritten in any language (Go, Rust, Node.js). It communicates only via HTTP APIs and has no knowledge of the server's internal implementation.

## Dependencies
- Python 3.13+
- requests 2.31+
- kubectl (installed in container)
- No Kubernetes Python client (use kubectl directly)

## Configuration

Environment variables:
- `KUBENTLY_API_URL`: Central API URL (required)
- `CLUSTER_ID`: Unique cluster identifier (required)
- `KUBENTLY_TOKEN`: Authentication token (required)
- `LOG_LEVEL`: Logging level (default: INFO)

## Implementation Requirements

### File Structure
```text
kubently/agent/
├── agent.py          # Single file implementation
├── Dockerfile
├── requirements.txt
└── README.md
```

### Implementation (`agent.py`)

```python
#!/usr/bin/env python3
"""
Kubently Agent - Kubernetes command executor.

This agent runs in a Kubernetes cluster and executes kubectl commands
received from the central Kubently API.
"""

import os
import sys
import time
import json
import subprocess
import logging
from typing import Dict, List, Optional
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

# Configure logging
logging.basicConfig(
    level=os.environ.get('LOG_LEVEL', 'INFO'),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('kubently-agent')

class KubentlyAgent:
    """Main agent class that polls for and executes commands."""
    
    def __init__(self):
        """Initialize agent with configuration from environment."""
        # Required configuration
        self.api_url = os.environ.get('KUBENTLY_API_URL')
        self.cluster_id = os.environ.get('CLUSTER_ID')
        self.token = os.environ.get('KUBENTLY_TOKEN')
        
        if not all([self.api_url, self.cluster_id, self.token]):
            logger.error("Missing required environment variables")
            sys.exit(1)
        
        
        # Setup HTTP session with retries
        self.session = self._create_session()
        
        # State
        self.consecutive_errors = 0
        self.is_active = False
        
        logger.info(f"Agent initialized for cluster: {self.cluster_id}")
    
    def _create_session(self) -> requests.Session:
        """
        Create HTTP session with retry logic and authentication.
        
        Returns:
            Configured requests Session
        """
        session = requests.Session()
        
        # Add authentication headers
        session.headers.update({
            'Authorization': f'Bearer {self.token}',
            'X-Cluster-ID': self.cluster_id,
            'User-Agent': 'Kubently-Agent/1.0'
        })
        
        # Configure retries
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[502, 503, 504],
            allowed_methods=["GET", "POST"]
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session
    
    def run(self) -> None:
        """
        Main agent loop.
        
        Continuously polls for commands and executes them.
        Adjusts polling rate based on session activity.
        """
        logger.info("Starting agent main loop")
        
        while True:
            try:
                # Determine poll wait time based on activity
                wait_time = self._get_wait_time()
                
                # Fetch commands with long polling
                commands = self._fetch_commands(wait_time)
                
                # Execute each command
                for command in commands:
                    self._execute_command(command)
                
                # Reset error counter on success
                self.consecutive_errors = 0
                
            except requests.RequestException as e:
                logger.error(f"Network error: {e}")
                self._handle_error()
                
            except Exception as e:
                logger.exception(f"Unexpected error: {e}")
                self._handle_error()
    
    def _get_wait_time(self) -> int:
        """
        Determine polling wait time.
        
        Returns:
            Wait time in seconds for long polling
        """
        # Check if cluster has active session
        try:
            response = self.session.get(
                f"{self.api_url}/agent/status",
                timeout=5
            )
            
            if response.status_code == 200:
                data = response.json()
                self.is_active = data.get('is_active', False)
                
                if self.is_active:
                    # Fast polling during active session
                    return 1  # 1 second wait for long poll
                    
        except Exception as e:
            logger.debug(f"Failed to check status: {e}")
        
        # Normal polling
        return 20  # 20 second wait for long poll
    
    def _fetch_commands(self, wait: int) -> List[Dict]:
        """
        Fetch commands from API using long polling.
        
        Args:
            wait: Seconds to wait for commands
            
        Returns:
            List of command dictionaries
        """
        try:
            response = self.session.get(
                f"{self.api_url}/agent/commands",
                params={'wait': wait},
                timeout=wait + 5  # Timeout slightly higher than wait
            )
            
            if response.status_code == 200:
                return response.json().get('commands', [])
            elif response.status_code == 204:
                # No content - no commands available
                return []
            else:
                logger.warning(f"Unexpected status code: {response.status_code}")
                return []
                
        except requests.Timeout:
            # Normal timeout from long polling
            return []
        except Exception as e:
            logger.error(f"Failed to fetch commands: {e}")
            raise
    
    def _execute_command(self, command: Dict) -> None:
        """
        Execute a single kubectl command.
        
        Args:
            command: Command dictionary with 'id', 'args', etc.
        """
        command_id = command.get('id', 'unknown')
        args = command.get('args', [])
        timeout = command.get('timeout', 10)
        
        logger.info(f"Executing command {command_id}: kubectl {' '.join(args)}")
        
        # Validate command
        if not self._validate_command(args):
            logger.warning(f"Invalid command rejected: {args}")
            self._send_result(command_id, {
                'success': False,
                'error': 'Command validation failed'
            })
            return
        
        # Execute kubectl
        result = self._run_kubectl(args, timeout)
        
        # Send result back
        self._send_result(command_id, result)
    
    def _validate_command(self, args: List[str]) -> bool:
        """
        Validate kubectl command for safety.
        
        Args:
            args: kubectl arguments
            
        Returns:
            True if command is safe to execute
        """
        if not args:
            return False
        
        # Whitelist of allowed read-only verbs
        allowed_verbs = {
            'get', 'describe', 'logs', 'top', 
            'api-resources', 'api-versions', 'explain', 
            'events', 'version'
        }
        
        # First arg should be an allowed verb
        verb = args[0]
        if verb not in allowed_verbs:
            logger.warning(f"Rejected verb: {verb}")
            return False
        
        # Reject any suspicious arguments
        forbidden_patterns = [
            '--token', '--kubeconfig', '--server',
            '--insecure', '--username', '--password',
            'exec', 'delete', 'edit', 'apply', 'create',
            'patch', 'replace', 'scale'
        ]
        
        for arg in args:
            if any(pattern in arg.lower() for pattern in forbidden_patterns):
                logger.warning(f"Rejected forbidden argument: {arg}")
                return False
        
        return True
    
    def _run_kubectl(self, args: List[str], timeout: int) -> Dict:
        """
        Execute kubectl command.
        
        Args:
            args: kubectl arguments
            timeout: Command timeout in seconds
            
        Returns:
            Result dictionary with success, output, error
        """
        start_time = time.time()
        
        try:
            # Build full command
            cmd = ['kubectl'] + args
            
            # Execute with timeout
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False  # Don't raise on non-zero exit
            )
            
            execution_time = int((time.time() - start_time) * 1000)
            
            return {
                'success': process.returncode == 0,
                'output': process.stdout,
                'error': process.stderr if process.returncode != 0 else None,
                'exit_code': process.returncode,
                'execution_time_ms': execution_time
            }
            
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'error': f'Command timed out after {timeout} seconds',
                'execution_time_ms': timeout * 1000
            }
        except Exception as e:
            return {
                'success': False,
                'error': f'Command execution failed: {str(e)}',
                'execution_time_ms': int((time.time() - start_time) * 1000)
            }
    
    def _send_result(self, command_id: str, result: Dict) -> None:
        """
        Send command result back to API.
        
        Args:
            command_id: Command identifier
            result: Execution result dictionary
        """
        try:
            response = self.session.post(
                f"{self.api_url}/agent/results",
                json={
                    'command_id': command_id,
                    'result': result
                },
                timeout=10
            )
            
            if response.status_code == 200:
                logger.info(f"Result sent for command {command_id}")
            else:
                logger.error(f"Failed to send result: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Failed to send result for {command_id}: {e}")
    
    def _handle_error(self) -> None:
        """Handle errors with exponential backoff."""
        self.consecutive_errors += 1
        
        # Exponential backoff with cap
        sleep_time = min(2 ** self.consecutive_errors, 60)
        
        logger.info(f"Sleeping {sleep_time}s after {self.consecutive_errors} errors")
        time.sleep(sleep_time)

def main():
    """Entry point."""
    try:
        agent = KubentlyAgent()
        agent.run()
    except KeyboardInterrupt:
        logger.info("Agent stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"Agent failed to start: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
```

### Dockerfile

```dockerfile
FROM python:3.13-alpine

# Install kubectl
RUN apk add --no-cache curl && \
    curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" && \
    chmod +x kubectl && \
    mv kubectl /usr/local/bin/ && \
    apk del curl

# Install Python dependencies
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Add agent
WORKDIR /app
COPY agent.py .

# Run as non-root
RUN adduser -D -u 1000 kubently
USER kubently

CMD ["python", "-u", "agent.py"]
```

### requirements.txt
```text
requests==2.31.0
```

## Security Considerations

1. **Read-only operations**: Agent only executes read commands
2. **Command validation**: Whitelist of allowed kubectl verbs
3. **No direct cluster access**: Uses pod's ServiceAccount
4. **Token authentication**: Unique token per cluster
5. **No sensitive data logging**: Sanitize logs

## Deployment

### Kubernetes Manifest
```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: kubently-agent
  namespace: kubently
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kubently-agent-readonly
rules:
- apiGroups: ["*"]
  resources: ["*"]
  verbs: ["get", "list", "watch"]
- nonResourceURLs: ["*"]
  verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: kubently-agent-readonly
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: kubently-agent-readonly
subjects:
- kind: ServiceAccount
  name: kubently-agent
  namespace: kubently
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kubently-agent
  namespace: kubently
spec:
  replicas: 1
  selector:
    matchLabels:
      app: kubently-agent
  template:
    metadata:
      labels:
        app: kubently-agent
    spec:
      serviceAccountName: kubently-agent
      containers:
      - name: agent
        image: kubently/agent:latest
        env:
        - name: KUBENTLY_API_URL
          value: "https://api.kubently.com"
        - name: CLUSTER_ID
          value: "production-cluster-1"
        - name: KUBENTLY_TOKEN
          valueFrom:
            secretKeyRef:
              name: kubently-agent-token
              key: token
        resources:
          requests:
            memory: "128Mi"
            cpu: "50m"
          limits:
            memory: "256Mi"
            cpu: "200m"
```

## Testing Requirements

### Unit Tests
```python
def test_validate_command():
    # Test command validation
    
def test_run_kubectl():
    # Test kubectl execution
    
def test_fetch_commands():
    # Test API polling
    
def test_error_handling():
    # Test error scenarios
```

### Integration Tests
- Test with real API
- Test with mock kubectl
- Test long polling behavior
- Test error recovery

## Performance Metrics

- Memory usage: < 100MB
- CPU usage: < 0.05 cores idle, < 0.2 cores active
- Command execution: < 500ms latency
- Network overhead: < 1KB per poll

## Deliverables

1. `agent.py` - Complete agent implementation
2. `Dockerfile` - Container image definition
3. `requirements.txt` - Python dependencies
4. `k8s-deployment.yaml` - Kubernetes manifests
5. Unit tests in `tests/`
6. Integration test suite
7. README with deployment instructions

## Development Notes

- Keep it simple - single file is fine
- Use kubectl directly, not Python client
- Focus on reliability over features
- Log everything for debugging
- Test with various kubectl commands
- Consider adding health endpoint
