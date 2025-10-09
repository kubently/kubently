# Cloud SDK Executor Architecture Plan - Black Box Design

## Executive Summary
This document defines the cloud executor architecture following Black Box design principles. Each executor is a replaceable module with a clean interface that hides implementation details. The system uses native SDKs (boto3 for AWS, google-cloud-python for GCP) to provide structured, type-safe cloud operations.

## Architecture Principles (Black Box Design)

### 1. Module Boundaries
Each component is a true black box:
- **CloudSDKExecutor**: Base interface defining what ALL executors must do
- **AWSSDKExecutor**: AWS-specific implementation (replaceable)
- **GCPSDKExecutor**: GCP-specific implementation (replaceable)
- **CloudToolRegistry**: Tool discovery and routing (replaceable)
- **SSEExecutor**: Command execution interface (replaceable)

### 2. Primitive Data Types
The system is built around simple primitives:
- **CloudTool**: Describes a single cloud operation
- **ExecutionResult**: Standard response format
- **Parameters**: Simple key-value pairs

### 3. Clean Interfaces
```python
# What the module DOES (interface)
class CloudSDKExecutor(ABC):
    @abstractmethod
    def execute_tool(self, tool: CloudTool, params: Dict) -> ExecutionResult:
        """Execute a cloud operation"""
        pass
    
    @abstractmethod
    def list_available_tools(self) -> List[CloudTool]:
        """List operations this executor can perform"""
        pass

# HOW is completely hidden in implementations
```

## Core Architecture

> **UPDATE**: Capability storage has been moved to Redis. See [UNIFIED_CAPABILITY_STORAGE.md](./UNIFIED_CAPABILITY_STORAGE.md) for the complete implementation of capability discovery and storage in Redis. This document focuses on the Cloud SDK executor implementation details.

### 1. Data Primitives

```python
from dataclasses import dataclass
from typing import Dict, List, Any, Optional

@dataclass
class CloudTool:
    """Single cloud operation primitive"""
    name: str                           # Tool identifier
    service: str                        # Service category
    operation: str                      # Operation name
    description: str                    # Human-readable description
    parameters: Dict[str, Any]          # Parameter schema
    required_permissions: List[str]     # IAM permissions needed

@dataclass
class ExecutionResult:
    """Standard result primitive"""
    success: bool
    data: Optional[Dict[str, Any]]
    error: Optional[str]
    error_code: Optional[str]
    tool: str
    service: str
```

### 2. Black Box Interface

```python
from abc import ABC, abstractmethod

class CloudSDKExecutor(ABC):
    """
    Black box interface for cloud executors.
    Implementation details are completely hidden.
    """
    
    @abstractmethod
    def execute_tool(self, tool: CloudTool, params: Dict) -> ExecutionResult:
        """
        Execute a cloud operation.
        
        What it does: Runs a cloud API operation
        What you need: Tool definition and parameters
        What you get: Standard execution result
        
        Implementation details (SDK, auth, retries) are hidden.
        """
        pass
    
    @abstractmethod
    def list_available_tools(self) -> List[CloudTool]:
        """
        List available operations.
        
        What it does: Returns operations this executor can perform
        What you need: Nothing
        What you get: List of tool definitions
        """
        pass
```

### 3. AWS Implementation (Replaceable Module)

```python
import boto3
from botocore.exceptions import ClientError

class AWSSDKExecutor(CloudSDKExecutor):
    """AWS-specific implementation - completely replaceable"""
    
    def __init__(self):
        # Internal implementation details hidden
        self._clients = {}
        self._session = boto3.Session()  # Uses IRSA automatically
    
    def execute_tool(self, tool: CloudTool, params: Dict) -> ExecutionResult:
        """Execute AWS operation - implementation hidden"""
        try:
            client = self._get_or_create_client(tool.service)
            operation = getattr(client, tool.operation)
            response = operation(**self._validate_params(tool, params))
            
            return ExecutionResult(
                success=True,
                data=self._clean_response(response),
                error=None,
                error_code=None,
                tool=tool.name,
                service=tool.service
            )
        except ClientError as e:
            return ExecutionResult(
                success=False,
                data=None,
                error=str(e),
                error_code=e.response['Error']['Code'],
                tool=tool.name,
                service=tool.service
            )
    
    def list_available_tools(self) -> List[CloudTool]:
        """Return AWS tools - can be replaced with config file later"""
        return [
            CloudTool(
                name="describe_instances",
                service="ec2",
                operation="describe_instances",
                description="List EC2 instances",
                parameters={"Filters": "list", "MaxResults": "int"},
                required_permissions=["ec2:DescribeInstances"]
            ),
            CloudTool(
                name="describe_cluster",
                service="eks",
                operation="describe_cluster",
                description="Get EKS cluster details",
                parameters={"name": "str"},
                required_permissions=["eks:DescribeCluster"]
            ),
            # More tools...
        ]
    
    # Private methods - implementation details
    def _get_or_create_client(self, service: str):
        """Hidden implementation detail"""
        if service not in self._clients:
            self._clients[service] = self._session.client(service)
        return self._clients[service]
    
    def _validate_params(self, tool: CloudTool, params: Dict) -> Dict:
        """Hidden implementation detail"""
        # Parameter validation logic
        pass
    
    def _clean_response(self, response: Dict) -> Dict:
        """Hidden implementation detail"""
        # Remove AWS metadata
        if 'ResponseMetadata' in response:
            del response['ResponseMetadata']
        return response
```

### 4. GCP Implementation (Replaceable Module)

```python
from google.cloud import compute_v1, container_v1
import google.auth

class GCPSDKExecutor(CloudSDKExecutor):
    """GCP-specific implementation - completely replaceable"""
    
    def __init__(self):
        # Internal implementation details hidden
        self._clients = {}
        self._credentials, self._project = google.auth.default()
    
    def execute_tool(self, tool: CloudTool, params: Dict) -> ExecutionResult:
        """Execute GCP operation - implementation hidden"""
        # GCP-specific implementation
        # Could be completely rewritten without affecting interface
        pass
    
    def list_available_tools(self) -> List[CloudTool]:
        """Return GCP tools"""
        return [
            CloudTool(
                name="list_instances",
                service="compute",
                operation="list_instances",
                description="List Compute instances",
                parameters={"project": "str", "zone": "str"},
                required_permissions=["compute.instances.list"]
            ),
            # More tools...
        ]
```

### 5. Tool Registry (Replaceable Module)

> **UPDATE**: The tool registry now integrates with Redis-based capability storage. Tools are discovered at executor startup and stored in Redis. See [UNIFIED_CAPABILITY_STORAGE.md](./UNIFIED_CAPABILITY_STORAGE.md) for details.

```python
class CloudToolRegistry:
    """
    Registry for tool discovery and routing.
    Completely replaceable - could use config files, database, etc.
    """
    
    def __init__(self):
        # Internal implementation hidden
        self._executors = {
            'aws': AWSSDKExecutor(),
            'gcp': GCPSDKExecutor()
        }
    
    def execute(self, provider: str, tool_name: str, params: Dict) -> ExecutionResult:
        """
        Route execution to appropriate provider.
        
        What it does: Executes a tool on specified provider
        What you need: Provider name, tool name, parameters
        What you get: Standard execution result
        """
        if provider not in self._executors:
            return ExecutionResult(
                success=False,
                data=None,
                error=f"Unknown provider: {provider}",
                error_code="UNKNOWN_PROVIDER",
                tool=tool_name,
                service="registry"
            )
        
        executor = self._executors[provider]
        tools = executor.list_available_tools()
        tool = self._find_tool(tools, tool_name)
        
        if not tool:
            return ExecutionResult(
                success=False,
                data=None,
                error=f"Unknown tool: {tool_name}",
                error_code="UNKNOWN_TOOL",
                tool=tool_name,
                service="registry"
            )
        
        return executor.execute_tool(tool, params)
    
    def list_all_tools(self) -> Dict[str, List[CloudTool]]:
        """List all available tools from all providers"""
        return {
            provider: executor.list_available_tools()
            for provider, executor in self._executors.items()
        }
    
    def _find_tool(self, tools: List[CloudTool], name: str) -> Optional[CloudTool]:
        """Hidden implementation detail"""
        return next((t for t in tools if t.name == name), None)
```

## Kubernetes Service Account Configuration

### 1. Service Account Setup

```yaml
# service-account.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: kubently-executor
  namespace: kubently
  annotations:
    # AWS IRSA annotation
    eks.amazonaws.com/role-arn: arn:aws:iam::ACCOUNT:role/kubently-executor-role
    # GCP Workload Identity annotation
    iam.gke.io/gcp-service-account: kubently-executor@PROJECT.iam.gserviceaccount.com
```

### 2. AWS IAM Role (Read-Only)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:Describe*",
        "eks:Describe*",
        "eks:List*",
        "logs:FilterLogEvents",
        "logs:GetLogEvents",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:ListMetrics",
        "iam:GetRole",
        "iam:GetRolePolicy",
        "s3:ListBucket",
        "s3:GetObject"
      ],
      "Resource": "*"
    }
  ]
}
```

Trust relationship for IRSA:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::ACCOUNT:oidc-provider/OIDC_PROVIDER"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "OIDC_PROVIDER:sub": "system:serviceaccount:kubently:kubently-executor",
          "OIDC_PROVIDER:aud": "sts.amazonaws.com"
        }
      }
    }
  ]
}
```

### 3. GCP Service Account (Read-Only)

```bash
# Create GCP service account
gcloud iam service-accounts create kubently-executor \
    --display-name="Kubently Executor (Read-Only)"

# Grant read-only roles
gcloud projects add-iam-policy-binding PROJECT_ID \
    --member="serviceAccount:kubently-executor@PROJECT.iam.gserviceaccount.com" \
    --role="roles/viewer"

gcloud projects add-iam-policy-binding PROJECT_ID \
    --member="serviceAccount:kubently-executor@PROJECT.iam.gserviceaccount.com" \
    --role="roles/container.viewer"

gcloud projects add-iam-policy-binding PROJECT_ID \
    --member="serviceAccount:kubently-executor@PROJECT.iam.gserviceaccount.com" \
    --role="roles/logging.viewer"

gcloud projects add-iam-policy-binding PROJECT_ID \
    --member="serviceAccount:kubently-executor@PROJECT.iam.gserviceaccount.com" \
    --role="roles/monitoring.viewer"

# Enable Workload Identity binding
gcloud iam service-accounts add-iam-policy-binding \
    kubently-executor@PROJECT.iam.gserviceaccount.com \
    --role roles/iam.workloadIdentityUser \
    --member "serviceAccount:PROJECT.svc.id.goog[kubently/kubently-executor]"
```

## Executor Pod Deployment

### 1. Deployment Configuration

```yaml
# executor-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kubently-executor
  namespace: kubently
spec:
  replicas: 3  # For high availability
  selector:
    matchLabels:
      app: kubently-executor
  template:
    metadata:
      labels:
        app: kubently-executor
    spec:
      serviceAccountName: kubently-executor
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        fsGroup: 1000
      containers:
      - name: executor
        image: kubently/executor:latest
        imagePullPolicy: Always
        env:
        - name: EXECUTOR_MODE
          value: "sdk"
        - name: LOG_LEVEL
          value: "INFO"
        - name: API_ENDPOINT
          value: "http://kubently-api:8080"
        # AWS IRSA environment variables (auto-injected)
        # - AWS_ROLE_ARN
        # - AWS_WEB_IDENTITY_TOKEN_FILE
        # GCP Workload Identity (auto-configured)
        resources:
          requests:
            memory: "256Mi"
            cpu: "100m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 10
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /ready
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 10
        securityContext:
          allowPrivilegeEscalation: false
          readOnlyRootFilesystem: true
          capabilities:
            drop:
            - ALL
        volumeMounts:
        - name: tmp
          mountPath: /tmp
      volumes:
      - name: tmp
        emptyDir: {}
```

### 2. Dockerfile

```dockerfile
# Dockerfile
FROM python:3.11-slim

# Security: Run as non-root user
RUN groupadd -r kubently && useradd -r -g kubently kubently

# Install dependencies
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY --chown=kubently:kubently . .

# Security: No write access except /tmp
USER kubently

EXPOSE 8080
CMD ["python", "-m", "kubently.executor"]
```

### 3. Requirements

```txt
# requirements.txt
boto3==1.28.0
google-cloud-compute==1.14.0
google-cloud-container==2.30.0
google-cloud-logging==3.5.0
google-cloud-monitoring==2.15.0
google-cloud-storage==2.10.0
kubernetes==27.2.0
aiohttp==3.8.5
pydantic==2.3.0
```

### 4. Helm Chart Values

```yaml
# helm/values.yaml
executor:
  enabled: true
  replicaCount: 3
  image:
    repository: kubently/executor
    tag: latest
    pullPolicy: Always
  
  serviceAccount:
    create: true
    name: kubently-executor
    annotations:
      # AWS IRSA
      eks.amazonaws.com/role-arn: "arn:aws:iam::ACCOUNT:role/kubently-executor-role"
      # GCP Workload Identity
      iam.gke.io/gcp-service-account: "kubently-executor@PROJECT.iam.gserviceaccount.com"
  
  resources:
    requests:
      memory: "256Mi"
      cpu: "100m"
    limits:
      memory: "512Mi"
      cpu: "500m"
  
  securityContext:
    runAsNonRoot: true
    runAsUser: 1000
    fsGroup: 1000
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    capabilities:
      drop:
      - ALL
  
  autoscaling:
    enabled: true
    minReplicas: 2
    maxReplicas: 10
    targetCPUUtilizationPercentage: 80
```

## Implementation Steps

### Phase 1: Setup Infrastructure
1. Create AWS IAM role with read-only permissions
2. Create GCP service account with viewer roles
3. Configure IRSA/Workload Identity bindings
4. Create Kubernetes service account with annotations

### Phase 2: Deploy Base System
1. Build executor container image
2. Deploy using Helm chart
3. Verify service account token mounting
4. Test AWS/GCP authentication

### Phase 3: Validate Permissions
1. Test AWS SDK calls (describe operations only)
2. Test GCP SDK calls (list/get operations only)
3. Verify no write operations are possible
4. Audit CloudTrail/GCP logs for access patterns

### Phase 4: Integration
1. Connect executors to Kubently API
2. Configure SSE endpoints
3. Test end-to-end cloud operations
4. Monitor performance and errors

## Security Considerations

### 1. Principle of Least Privilege
- Only read operations permitted
- No write, delete, or modify permissions
- Scoped to specific services needed

### 2. Authentication
- AWS: IRSA with OIDC provider
- GCP: Workload Identity with service account
- No long-lived credentials in pods

### 3. Network Security
- Executors only communicate with Kubently API
- No direct external access
- All traffic over TLS

### 4. Container Security
- Run as non-root user
- Read-only root filesystem
- No privilege escalation
- Minimal base image

## Monitoring and Observability

### 1. Metrics
- Tool execution count
- Success/failure rates
- Response times
- Permission denied errors

### 2. Logging
- Structured JSON logging
- Request/response tracking
- Error details with stack traces
- Audit trail of all operations

### 3. Tracing
- Distributed tracing with OpenTelemetry
- Correlation IDs for request tracking
- Performance bottleneck identification

## Testing Strategy

### 1. Unit Tests
```python
def test_aws_executor_interface():
    """Test that AWS executor implements interface correctly"""
    executor = AWSSDKExecutor()
    assert hasattr(executor, 'execute_tool')
    assert hasattr(executor, 'list_available_tools')
    
def test_replaceable_executor():
    """Test that executor can be replaced"""
    class MockExecutor(CloudSDKExecutor):
        def execute_tool(self, tool, params):
            return ExecutionResult(success=True, data={}, ...)
        def list_available_tools(self):
            return []
    
    # Should work identically to real executor
    mock = MockExecutor()
    result = mock.execute_tool(tool, {})
    assert result.success
```

### 2. Integration Tests
- Test with real AWS/GCP APIs in dev environment
- Verify permission boundaries
- Test error handling for access denied

### 3. Black Box Tests
- Test only through public interfaces
- No knowledge of implementation required
- Verify contract compliance

## Benefits of This Approach

### 1. True Black Box Design
- Each module has a clean interface
- Implementation details completely hidden
- Modules are independently replaceable

### 2. Maintainability
- Clear module boundaries
- Single responsibility per module
- Easy to understand and modify

### 3. Security
- Type-safe operations
- No command injection risks
- Centralized permission management

### 4. Flexibility
- Easy to add new cloud providers
- Simple to add new operations
- Can switch implementations without breaking system

## Conclusion

This architecture follows Black Box design principles by:
- Defining clear interfaces that hide implementation
- Using simple primitives (CloudTool, ExecutionResult)
- Making every module replaceable
- Optimizing for human understanding over code complexity

The system is secure, maintainable, and can evolve without breaking existing functionality.