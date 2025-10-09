# Unified Capability Storage Plan - Redis Integration

## Executive Summary
This plan merges the Dynamic Discovery and Cloud SDK Executor capabilities into a unified Redis-based storage system. Executors push their complete capability set (kubectl verbs, cloud SDK tools) to Redis on startup, eliminating the need for meta-command queries and providing instant capability checking for the A2A agent.

## Architecture Principles

### 1. Push-Based Model
- Executors announce capabilities on startup
- No hot-reload mechanism (pod restart required for changes)
- Capabilities stored with cluster data in Redis
- Agent reads capabilities directly from Redis (no executor queries)

### 2. Data Structure in Redis

```python
# Redis Key Structure
# cluster:<cluster_id>:info - Basic cluster information
# cluster:<cluster_id>:capabilities - Executor capabilities

@dataclass
class ExecutorCapabilities:
    """Complete capability definition for an executor"""
    cluster_id: str
    timestamp: datetime
    
    # Kubernetes capabilities
    security_mode: str  # readOnly, extendedReadOnly, readWrite
    allowed_kubectl_verbs: List[str]  # ['get', 'describe', 'logs', etc.]
    resource_restrictions: Dict[str, List[str]]  # {'secrets': [], 'configmaps': ['get']}
    
    # Cloud SDK capabilities
    cloud_provider: Optional[str]  # 'aws', 'gcp', 'azure', None
    cloud_tools: List[CloudTool]  # Available SDK operations
    iam_permissions: List[str]  # Effective IAM permissions
    
    # Executor metadata
    executor_version: str
    executor_pod: str
    service_account: str
    
    # Feature flags
    features: Dict[str, bool]  # {'port_forward': False, 'exec': False}

@dataclass
class CloudTool:
    """Single cloud operation capability"""
    name: str                           # Tool identifier
    service: str                        # Service category (ec2, eks, compute)
    operation: str                      # Operation name
    description: str                    # Human-readable description
    parameters: Dict[str, Any]          # Parameter schema
    required_permissions: List[str]     # IAM permissions needed
```

### 3. Redis Storage Implementation

```python
import json
import redis
from typing import Optional
from datetime import datetime, timedelta

class CapabilityStorage:
    """Manages capability storage in Redis"""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.ttl = 3600  # 1 hour TTL, refreshed on heartbeat
    
    def store_capabilities(self, cluster_id: str, capabilities: ExecutorCapabilities) -> bool:
        """Store executor capabilities in Redis"""
        key = f"cluster:{cluster_id}:capabilities"
        
        # Serialize capabilities
        data = {
            'cluster_id': cluster_id,
            'timestamp': capabilities.timestamp.isoformat(),
            'security_mode': capabilities.security_mode,
            'allowed_kubectl_verbs': capabilities.allowed_kubectl_verbs,
            'resource_restrictions': capabilities.resource_restrictions,
            'cloud_provider': capabilities.cloud_provider,
            'cloud_tools': [self._serialize_tool(t) for t in capabilities.cloud_tools],
            'iam_permissions': capabilities.iam_permissions,
            'executor_version': capabilities.executor_version,
            'executor_pod': capabilities.executor_pod,
            'service_account': capabilities.service_account,
            'features': capabilities.features
        }
        
        # Store with TTL
        return self.redis.setex(
            key,
            self.ttl,
            json.dumps(data)
        )
    
    def get_capabilities(self, cluster_id: str) -> Optional[ExecutorCapabilities]:
        """Retrieve executor capabilities from Redis"""
        key = f"cluster:{cluster_id}:capabilities"
        
        data = self.redis.get(key)
        if not data:
            return None
        
        # Deserialize and return
        return self._deserialize_capabilities(json.loads(data))
    
    def refresh_ttl(self, cluster_id: str) -> bool:
        """Refresh TTL on heartbeat"""
        key = f"cluster:{cluster_id}:capabilities"
        return self.redis.expire(key, self.ttl)
    
    def _serialize_tool(self, tool: CloudTool) -> dict:
        """Serialize CloudTool to dict"""
        return {
            'name': tool.name,
            'service': tool.service,
            'operation': tool.operation,
            'description': tool.description,
            'parameters': tool.parameters,
            'required_permissions': tool.required_permissions
        }
```

## Executor Implementation

### 1. Capability Discovery on Startup

```python
# kubently/modules/executor/capability_reporter.py

from dataclasses import dataclass
from typing import List, Dict, Optional
import boto3
from google.cloud import compute_v1
from .dynamic_whitelist import DynamicCommandWhitelist

class CapabilityReporter:
    """Reports executor capabilities to central API"""
    
    def __init__(self, cluster_id: str, whitelist: DynamicCommandWhitelist):
        self.cluster_id = cluster_id
        self.whitelist = whitelist
        self.cloud_provider = self._detect_cloud_provider()
    
    def gather_capabilities(self) -> ExecutorCapabilities:
        """Gather all executor capabilities"""
        
        # Get kubectl capabilities from whitelist
        kubectl_config = self.whitelist.get_config_summary()
        
        # Discover cloud SDK capabilities
        cloud_tools = []
        iam_permissions = []
        
        if self.cloud_provider == 'aws':
            cloud_tools = self._discover_aws_tools()
            iam_permissions = self._get_aws_permissions()
        elif self.cloud_provider == 'gcp':
            cloud_tools = self._discover_gcp_tools()
            iam_permissions = self._get_gcp_permissions()
        
        return ExecutorCapabilities(
            cluster_id=self.cluster_id,
            timestamp=datetime.utcnow(),
            security_mode=kubectl_config['mode'],
            allowed_kubectl_verbs=kubectl_config['allowed_verbs'],
            resource_restrictions=kubectl_config.get('resource_restrictions', {}),
            cloud_provider=self.cloud_provider,
            cloud_tools=cloud_tools,
            iam_permissions=iam_permissions,
            executor_version=self._get_version(),
            executor_pod=os.environ.get('HOSTNAME', 'unknown'),
            service_account=self._get_service_account(),
            features=self._get_feature_flags()
        )
    
    def _detect_cloud_provider(self) -> Optional[str]:
        """Detect which cloud provider we're running on"""
        # Check for AWS
        if os.environ.get('AWS_ROLE_ARN') or os.environ.get('AWS_WEB_IDENTITY_TOKEN_FILE'):
            return 'aws'
        
        # Check for GCP
        try:
            import google.auth
            credentials, project = google.auth.default()
            if credentials:
                return 'gcp'
        except:
            pass
        
        # Check for Azure
        if os.environ.get('AZURE_CLIENT_ID'):
            return 'azure'
        
        return None
    
    def _discover_aws_tools(self) -> List[CloudTool]:
        """Discover available AWS SDK tools"""
        tools = []
        
        # Test which AWS services we can access
        session = boto3.Session()
        
        # EC2
        try:
            ec2 = session.client('ec2')
            ec2.describe_instances(MaxResults=1)
            tools.append(CloudTool(
                name="describe_instances",
                service="ec2",
                operation="describe_instances",
                description="List EC2 instances",
                parameters={"Filters": "list", "MaxResults": "int"},
                required_permissions=["ec2:DescribeInstances"]
            ))
        except:
            pass
        
        # EKS
        try:
            eks = session.client('eks')
            eks.list_clusters(maxResults=1)
            tools.extend([
                CloudTool(
                    name="list_clusters",
                    service="eks",
                    operation="list_clusters",
                    description="List EKS clusters",
                    parameters={"maxResults": "int"},
                    required_permissions=["eks:ListClusters"]
                ),
                CloudTool(
                    name="describe_cluster",
                    service="eks",
                    operation="describe_cluster",
                    description="Get EKS cluster details",
                    parameters={"name": "str"},
                    required_permissions=["eks:DescribeCluster"]
                )
            ])
        except:
            pass
        
        return tools
    
    def _get_feature_flags(self) -> Dict[str, bool]:
        """Get feature flags based on security mode"""
        mode = self.whitelist.get_config_summary()['mode']
        
        return {
            'port_forward': mode in ['readWrite', 'admin'],
            'exec': mode in ['readWrite', 'admin'],
            'proxy': mode in ['extendedReadOnly', 'readWrite', 'admin'],
            'cloud_sdk': self.cloud_provider is not None,
            'logs_streaming': True,
            'metrics': mode != 'minimal'
        }
```

### 2. Executor Startup Flow

```python
# kubently/modules/executor/sse_executor.py

class SSEExecutor:
    """Enhanced executor with capability reporting"""
    
    async def start(self):
        """Start executor and report capabilities"""
        
        # Initialize components
        self.whitelist = DynamicCommandWhitelist(mode=self.security_mode)
        self.reporter = CapabilityReporter(self.cluster_id, self.whitelist)
        
        # Gather capabilities
        capabilities = self.reporter.gather_capabilities()
        
        # Report to central API (which stores in Redis)
        await self._report_capabilities(capabilities)
        
        # Start heartbeat to refresh TTL
        asyncio.create_task(self._heartbeat_loop())
        
        # Start main SSE loop
        await self._sse_loop()
    
    async def _report_capabilities(self, capabilities: ExecutorCapabilities):
        """Report capabilities to central API"""
        async with aiohttp.ClientSession() as session:
            url = f"{self.api_endpoint}/api/v1/clusters/{self.cluster_id}/capabilities"
            
            payload = self._serialize_capabilities(capabilities)
            
            async with session.post(url, json=payload) as response:
                if response.status != 200:
                    logger.error(f"Failed to report capabilities: {response.status}")
                else:
                    logger.info(f"Successfully reported capabilities for {self.cluster_id}")
    
    async def _heartbeat_loop(self):
        """Refresh capability TTL periodically"""
        while True:
            await asyncio.sleep(300)  # Every 5 minutes
            
            try:
                await self._send_heartbeat()
            except Exception as e:
                logger.error(f"Heartbeat failed: {e}")
```

## Agent Implementation

### 1. Capability-Aware Tool Execution

```python
# kubently/modules/a2a/protocol_bindings/a2a_server/agent.py

class CapabilityAwareAgent:
    """Agent that checks capabilities before executing tools"""
    
    def __init__(self, redis_client: redis.Redis):
        self.storage = CapabilityStorage(redis_client)
        self.capability_cache = {}  # In-memory cache for session
    
    async def check_capability(self, cluster_id: str, operation: str) -> bool:
        """Check if operation is allowed for cluster"""
        
        # Check cache first
        if cluster_id not in self.capability_cache:
            capabilities = self.storage.get_capabilities(cluster_id)
            if not capabilities:
                logger.warning(f"No capabilities found for cluster {cluster_id}")
                return False
            self.capability_cache[cluster_id] = capabilities
        
        caps = self.capability_cache[cluster_id]
        
        # Parse operation type
        if operation.startswith('kubectl'):
            verb = operation.split()[1] if len(operation.split()) > 1 else None
            return verb in caps.allowed_kubectl_verbs
        
        elif operation.startswith('cloud:'):
            # cloud:aws:describe_instances
            parts = operation.split(':')
            if len(parts) >= 3:
                provider, tool_name = parts[1], parts[2]
                if caps.cloud_provider != provider:
                    return False
                return any(t.name == tool_name for t in caps.cloud_tools)
        
        return False
    
    @tool
    async def execute_kubectl(
        self,
        cluster_id: str,
        command: str,
        resource: str,
        namespace: str = "default"
    ) -> str:
        """Execute kubectl command with capability checking"""
        
        # Check capability first
        full_command = f"kubectl {command} {resource}"
        if not await self.check_capability(cluster_id, full_command):
            return f"Operation '{command}' is not allowed for cluster {cluster_id}. Check cluster capabilities."
        
        # Execute if allowed
        return await self._execute_command(cluster_id, full_command, namespace)
    
    @tool
    async def get_cluster_capabilities(self, cluster_id: str) -> str:
        """Get capabilities for a cluster from Redis"""
        
        capabilities = self.storage.get_capabilities(cluster_id)
        
        if not capabilities:
            return f"No capabilities found for cluster {cluster_id}. Executor may be offline."
        
        # Format for LLM understanding
        return f"""
Cluster: {cluster_id}
Security Mode: {capabilities.security_mode}
Allowed kubectl verbs: {', '.join(capabilities.allowed_kubectl_verbs)}
Cloud Provider: {capabilities.cloud_provider or 'None'}
Cloud Tools: {len(capabilities.cloud_tools)} available
Features:
- Port Forward: {capabilities.features.get('port_forward', False)}
- Exec: {capabilities.features.get('exec', False)}
- Cloud SDK: {capabilities.features.get('cloud_sdk', False)}
Last Updated: {capabilities.timestamp}
"""
```

### 2. Enhanced System Prompt

```yaml
# prompts/system.prompt.yaml
content: |
  You are an A2A agent that manages Kubernetes clusters with capability awareness.
  
  Core Rules:
  - CAPABILITY AWARENESS: Always check cluster capabilities before attempting operations
  - Use get_cluster_capabilities at the start of each conversation for a new cluster
  - Never attempt operations not listed in the cluster's allowed_kubectl_verbs
  - Respect security modes: readOnly clusters cannot modify resources
  - Cloud SDK operations require matching cloud_provider in capabilities
  
  Capability Checking Protocol:
  1. When asked to perform an operation on a cluster for the first time:
     - Call get_cluster_capabilities(cluster_id) 
     - Cache the result mentally for the conversation
  2. Before each operation, verify it's in the allowed list
  3. If operation is denied, explain the security restriction to the user
  
  Security Modes:
  - minimal: Only basic read operations (get, list)
  - readOnly: Read operations including logs and describe
  - extendedReadOnly: Read operations plus proxy access
  - readWrite: All operations except admin tasks
  - admin: Full access including exec and port-forward
```

## API Endpoint Implementation

```python
# kubently/modules/api/capability_endpoint.py

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import redis

router = APIRouter()
redis_client = redis.Redis(host='redis', port=6379, decode_responses=True)
storage = CapabilityStorage(redis_client)

class CapabilityReport(BaseModel):
    """Capability report from executor"""
    cluster_id: str
    security_mode: str
    allowed_kubectl_verbs: List[str]
    cloud_provider: Optional[str]
    cloud_tools: List[dict]
    features: Dict[str, bool]
    executor_version: str
    executor_pod: str
    service_account: str

@router.post("/api/v1/clusters/{cluster_id}/capabilities")
async def report_capabilities(cluster_id: str, report: CapabilityReport):
    """Receive capability report from executor"""
    
    # Convert to ExecutorCapabilities
    capabilities = ExecutorCapabilities(
        cluster_id=cluster_id,
        timestamp=datetime.utcnow(),
        security_mode=report.security_mode,
        allowed_kubectl_verbs=report.allowed_kubectl_verbs,
        resource_restrictions={},  # TODO: Add if needed
        cloud_provider=report.cloud_provider,
        cloud_tools=[CloudTool(**t) for t in report.cloud_tools],
        iam_permissions=[],  # TODO: Add if needed
        executor_version=report.executor_version,
        executor_pod=report.executor_pod,
        service_account=report.service_account,
        features=report.features
    )
    
    # Store in Redis
    if storage.store_capabilities(cluster_id, capabilities):
        return {"status": "success", "message": "Capabilities stored"}
    else:
        raise HTTPException(status_code=500, detail="Failed to store capabilities")

@router.post("/api/v1/clusters/{cluster_id}/heartbeat")
async def heartbeat(cluster_id: str):
    """Refresh capability TTL"""
    if storage.refresh_ttl(cluster_id):
        return {"status": "success", "message": "TTL refreshed"}
    else:
        raise HTTPException(status_code=404, detail="Cluster not found")

@router.get("/api/v1/clusters/{cluster_id}/capabilities")
async def get_capabilities(cluster_id: str):
    """Get capabilities for a cluster"""
    capabilities = storage.get_capabilities(cluster_id)
    
    if not capabilities:
        raise HTTPException(status_code=404, detail="Capabilities not found")
    
    return capabilities
```

## Migration Path

### Phase 1: Redis Infrastructure (Week 1)
- [ ] Deploy Redis with persistence enabled
- [ ] Configure Redis connection in API and executors
- [ ] Implement CapabilityStorage class
- [ ] Add capability endpoints to API

### Phase 2: Executor Updates (Week 2)
- [ ] Implement CapabilityReporter in executors
- [ ] Add startup capability reporting
- [ ] Implement heartbeat mechanism
- [ ] Test with different security modes

### Phase 3: Agent Integration (Week 3)
- [ ] Update agent to use Redis capabilities
- [ ] Remove meta-command implementation
- [ ] Update system prompts
- [ ] Add capability checking to all tools

### Phase 4: Cloud SDK Integration (Week 4)
- [ ] Implement AWS tool discovery
- [ ] Implement GCP tool discovery
- [ ] Add cloud tools to capability reports
- [ ] Test cloud operations with agent

## Benefits of Redis Approach

### 1. Performance
- Sub-millisecond capability lookups
- No network round-trips to executors
- In-memory caching for hot paths
- Horizontal scaling with Redis Cluster

### 2. Reliability
- Executors can restart without losing state
- Capabilities persist across pod restarts
- TTL ensures stale data is removed
- Redis replication for high availability

### 3. Simplicity
- Single source of truth for capabilities
- No complex discovery protocol
- Clear data model
- Easy debugging with Redis CLI

### 4. Extensibility
- Easy to add new capability types
- Version compatibility through schema evolution
- Feature flags for gradual rollout
- Abstraction layer for future migration if needed

## Monitoring and Observability

```python
# Metrics to track
metrics = {
    'capability_reports_total': Counter('capability_reports_total', 'Total capability reports received'),
    'capability_lookups_total': Counter('capability_lookups_total', 'Total capability lookups'),
    'capability_cache_hits': Counter('capability_cache_hits', 'Capability cache hit rate'),
    'capability_ttl_refreshes': Counter('capability_ttl_refreshes', 'TTL refresh count'),
    'capability_staleness_seconds': Histogram('capability_staleness', 'Age of capability data')
}
```

## Security Considerations

### 1. Data Validation
- Validate all capability reports
- Sanitize cloud tool definitions
- Verify executor identity

### 2. Access Control
- Only executors can write capabilities
- Agents have read-only access
- API validates executor tokens

### 3. Data Privacy
- No sensitive data in capabilities
- IAM permissions listed but not credentials
- Service account names without tokens

## Conclusion

This unified approach leverages Redis as the central capability store, providing:
- **Immediate capability checking** without executor queries
- **Simple push-based model** aligned with no hot-reload constraint
- **Unified storage** for both kubectl and cloud SDK capabilities
- **Clear migration path** from current architecture
- **Future flexibility** through abstraction layers

The system maintains security boundaries while enabling efficient, capability-aware operations across the distributed Kubently architecture.