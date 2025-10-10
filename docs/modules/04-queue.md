# Module: Command Queue

## Black Box Interface

**Purpose**: Queue commands and deliver results between API and agents

**What this module does** (Public Interface):
- Queues commands for cluster agents
- Retrieves commands with optional blocking
- Stores command results
- Retrieves results with timeout

**What this module hides** (Implementation):
- Queue backend (Redis, RabbitMQ, Kafka)
- Blocking/polling mechanisms
- Result storage and TTL
- Queue ordering guarantees
- Serialization format

## Overview
The Queue module is a black box for command/result exchange. It can be completely replaced with any message queue system without affecting other modules.

## Dependencies
- Redis client (provided by API Core)
- Python 3.13+ standard library (json, asyncio, uuid)

## Interfaces

### Public Methods

```python
class QueueModule:
    async def push_command(self, cluster_id: str, command: dict) -> str:
        """Add command to cluster queue"""
        
    async def pull_commands(self, cluster_id: str, wait: int = 0) -> List[dict]:
        """Get commands with optional blocking wait"""
        
    async def store_result(self, command_id: str, result: dict) -> None:
        """Store command execution result"""
        
    async def wait_for_result(self, command_id: str, timeout: int = 10) -> Optional[dict]:
        """Wait for command result (blocking)"""
        
    async def get_queue_depth(self, cluster_id: str) -> int:
        """Get number of pending commands"""
```

## Implementation Requirements

### File Structure
```text
kubently/api/
└── queue.py
```

### Implementation (`queue.py`)

```python
import json
import asyncio
import uuid
from typing import List, Optional
from datetime import datetime

class QueueModule:
    def __init__(self, redis_client, max_commands_per_fetch: int = 10):
        """
        Initialize queue module.
        
        Args:
            redis_client: Async Redis client
            max_commands_per_fetch: Max commands to return per fetch
        """
        self.redis = redis_client
        self.max_commands_per_fetch = max_commands_per_fetch
    
    async def push_command(self, cluster_id: str, command: dict) -> str:
        """
        Add command to cluster's queue.
        
        Args:
            cluster_id: Target cluster
            command: Command dict with at least 'id' and 'args'
            
        Returns:
            Command ID
            
        Logic:
        1. Ensure command has ID
        2. Add timestamp
        3. Push to cluster queue (LPUSH for FIFO with RPOP)
        4. Set expiration on queue
        """
        # Ensure command has ID
        if 'id' not in command:
            command['id'] = str(uuid.uuid4())
        
        # Add metadata
        command['queued_at'] = datetime.utcnow().isoformat()
        command['cluster_id'] = cluster_id
        
        # Queue key
        queue_key = f"queue:commands:{cluster_id}"
        
        # Push to queue (LPUSH + RPOP = FIFO)
        await self.redis.lpush(queue_key, json.dumps(command))
        
        # Set expiration (queue expires if idle)
        await self.redis.expire(queue_key, 300)  # 5 minutes
        
        # Track command for result waiting
        tracking_key = f"command:tracking:{command['id']}"
        await self.redis.setex(
            tracking_key,
            60,  # 1 minute tracking
            json.dumps({
                "cluster_id": cluster_id,
                "queued_at": command['queued_at']
            })
        )
        
        # Update metrics
        await self._increment_metric("commands_queued", cluster_id)
        
        return command['id']
    
    async def pull_commands(self, cluster_id: str, wait: int = 0) -> List[dict]:
        """
        Pull commands from queue with optional blocking.
        
        THIS IS THE KEY METHOD FOR LOW LATENCY.
        
        Args:
            cluster_id: Cluster identifier  
            wait: Seconds to wait for commands (0 = non-blocking)
            
        Returns:
            List of commands (empty if none available)
            
        Logic for blocking mode (wait > 0):
        1. Use BRPOP for blocking right pop
        2. Returns immediately when command available
        3. Returns None after timeout
        
        Logic for non-blocking mode (wait = 0):
        1. Use RPOP to get multiple commands
        2. Return immediately
        """
        queue_key = f"queue:commands:{cluster_id}"
        commands = []
        
        if wait > 0:
            # BLOCKING MODE - Wait for single command
            try:
                # BRPOP blocks until item available or timeout
                result = await self.redis.brpop(queue_key, timeout=wait)
                
                if result:
                    # result is tuple: (key, value)
                    command = json.loads(result[1])
                    commands.append(command)
                    
                    # Log immediate delivery
                    await self._record_latency(command)
                    
            except asyncio.TimeoutError:
                # No commands within timeout
                pass
                
        else:
            # NON-BLOCKING MODE - Get batch
            for _ in range(self.max_commands_per_fetch):
                cmd_json = await self.redis.rpop(queue_key)
                if not cmd_json:
                    break
                    
                command = json.loads(cmd_json)
                commands.append(command)
                
                # Record latency
                await self._record_latency(command)
        
        # Update metrics
        if commands:
            await self._increment_metric("commands_delivered", cluster_id, len(commands))
        
        return commands
    
    async def store_result(self, command_id: str, result: dict) -> None:
        """
        Store command execution result.
        
        Args:
            command_id: Command identifier
            result: Execution result dict
            
        Logic:
        1. Store result with TTL
        2. Publish notification for waiters
        3. Update metrics
        """
        # Add metadata
        result['stored_at'] = datetime.utcnow().isoformat()
        
        # Store result
        result_key = f"result:{command_id}"
        await self.redis.setex(
            result_key,
            60,  # 1 minute TTL
            json.dumps(result)
        )
        
        # Notify any waiters via pub/sub
        channel = f"result:ready:{command_id}"
        await self.redis.publish(channel, "1")
        
        # Update tracking
        tracking_key = f"command:tracking:{command_id}"
        tracking = await self.redis.get(tracking_key)
        if tracking:
            tracking_data = json.loads(tracking)
            cluster_id = tracking_data.get('cluster_id')
            
            # Record execution metrics
            if result.get('success'):
                await self._increment_metric("commands_succeeded", cluster_id)
            else:
                await self._increment_metric("commands_failed", cluster_id)
    
    async def wait_for_result(self, command_id: str, timeout: int = 10) -> Optional[dict]:
        """
        Wait for command result (blocking).
        
        Used by API to wait synchronously for command completion.
        
        Args:
            command_id: Command identifier
            timeout: Max seconds to wait
            
        Returns:
            Result dict or None if timeout
            
        Logic:
        1. Check if result already exists
        2. If not, poll with exponential backoff
        3. Could be optimized with pub/sub
        """
        result_key = f"result:{command_id}"
        
        # Check if already available
        result = await self.redis.get(result_key)
        if result:
            return json.loads(result)
        
        # Poll for result with exponential backoff
        elapsed = 0
        poll_interval = 0.1  # Start with 100ms
        
        while elapsed < timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            
            # Check for result
            result = await self.redis.get(result_key)
            if result:
                return json.loads(result)
            
            # Exponential backoff (cap at 1 second)
            poll_interval = min(poll_interval * 1.5, 1.0)
        
        # Timeout - record metric
        await self._increment_metric("commands_timeout", "global")
        return None
    
    async def get_queue_depth(self, cluster_id: str) -> int:
        """
        Get number of pending commands.
        
        Args:
            cluster_id: Cluster identifier
            
        Returns:
            Number of commands in queue
        """
        queue_key = f"queue:commands:{cluster_id}"
        return await self.redis.llen(queue_key)
    
    async def clear_queue(self, cluster_id: str) -> int:
        """
        Clear all pending commands for cluster.
        
        Used for emergency stop or cleanup.
        
        Args:
            cluster_id: Cluster identifier
            
        Returns:
            Number of commands cleared
        """
        queue_key = f"queue:commands:{cluster_id}"
        
        # Get count before clearing
        count = await self.redis.llen(queue_key)
        
        # Delete queue
        await self.redis.delete(queue_key)
        
        return count
    
    async def _record_latency(self, command: dict):
        """Record command delivery latency"""
        if 'queued_at' in command:
            queued_at = datetime.fromisoformat(command['queued_at'])
            latency_ms = (datetime.utcnow() - queued_at).total_seconds() * 1000
            
            # Store in Redis for monitoring
            await self.redis.lpush("metrics:delivery_latency", latency_ms)
            await self.redis.ltrim("metrics:delivery_latency", 0, 999)
    
    async def _increment_metric(self, metric: str, cluster_id: str, count: int = 1):
        """Increment counter metric"""
        key = f"metrics:{metric}:{cluster_id}"
        await self.redis.incrby(key, count)
        
        # Set expiration (metrics expire after 1 day)
        await self.redis.expire(key, 86400)
```

## Redis Operations Used

Critical operations for performance:
- `BRPOP`: Blocking pop for instant command delivery
- `LPUSH/RPOP`: FIFO queue implementation
- `SETEX`: Atomic set with expiration
- `PUBLISH`: Notify result waiters

## Performance Optimizations

1. **BRPOP for low latency**: Agent blocks waiting for commands (< 50ms delivery)
2. **Batch fetch**: Get multiple commands in one call
3. **Result polling**: Exponential backoff to reduce Redis load
4. **Auto-expiration**: All keys have TTL, no cleanup needed

## Testing Requirements

### Unit Tests
```python
async def test_push_and_pull_blocking():
    # Test blocking command delivery
    
async def test_push_and_pull_non_blocking():
    # Test batch command fetch
    
async def test_store_and_wait_for_result():
    # Test result storage and retrieval
    
async def test_queue_expiration():
    # Test automatic queue expiration
    
async def test_concurrent_operations():
    # Test with multiple agents pulling
```

### Performance Tests
```python
async def test_blocking_latency():
    # Measure BRPOP latency (target: < 50ms)
    
async def test_throughput():
    # Measure commands/second (target: > 1000/s)
```

## Error Handling

- Redis connection failure: Return empty list, log error
- JSON parse errors: Skip malformed commands
- Timeout: Return None for wait_for_result

## Deliverables

1. `queue.py` with QueueModule implementation
2. Unit tests in `tests/test_queue.py`
3. Performance benchmarks
4. Load test scripts

## Development Notes

- BRPOP is the key to low latency - test thoroughly
- Consider pub/sub for result waiting optimization
- Monitor queue depth for capacity planning
- Add circuit breaker for Redis failures
