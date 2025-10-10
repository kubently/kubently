# Dual Storage Backend Support Plan
## Redis + PostgreSQL Configurable Storage

### Executive Summary
This plan outlines the implementation strategy to support both Redis and PostgreSQL as configurable storage backends in Kubently, allowing users to choose based on their operational requirements.

### Current State Analysis

#### Redis Usage in Kubently
1. **Session Management**
   - Create, get, keep-alive sessions
   - Track active clusters for polling optimization
   - Correlation ID indexing for A2A chains

2. **Queue Operations**
   - Command queuing for agent execution
   - Asynchronous task distribution

3. **Authentication/Authorization**
   - Token storage and validation
   - Service identity management

4. **A2A Communication State**
   - Agent-to-agent protocol state
   - Correlation tracking across services

5. **Event Publishing/Monitoring**
   - Real-time event streaming via pub/sub
   - Event history storage

6. **Caching and Temporary Data**
   - Fast cluster status checks
   - Temporary operation state

### Architecture Design

#### Phase 1: Storage Abstraction Layer
```
┌─────────────────────────────────────────────────┐
│                 Application Layer                │
├─────────────────────────────────────────────────┤
│            Storage Abstraction Interface         │
│  ┌─────────────────────────────────────────┐   │
│  │ - get(key) -> value                     │   │
│  │ - set(key, value, ttl?)                 │   │
│  │ - delete(key)                           │   │
│  │ - exists(key) -> bool                   │   │
│  │ - expire(key, ttl)                      │   │
│  │ - publish(channel, message)             │   │
│  │ - subscribe(channel) -> messages        │   │
│  │ - atomic_operations()                   │   │
│  └─────────────────────────────────────────┘   │
├─────────────────────────────────────────────────┤
│                Backend Implementations           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐     │
│  │  Redis   │  │PostgreSQL│  │  Hybrid  │     │
│  │ Backend  │  │ Backend  │  │ Backend  │     │
│  └──────────┘  └──────────┘  └──────────┘     │
└─────────────────────────────────────────────────┘
```

#### Phase 2: Backend Implementation Details

##### RedisBackend (Current)
```python
class RedisBackend(StorageBackend):
    """Existing Redis implementation"""
    - Direct mapping of current functionality
    - No changes to performance characteristics
    - Maintains all existing features
```

##### PostgreSQLBackend (New)
```python
class PostgreSQLBackend(StorageBackend):
    """PostgreSQL implementation with Redis-like semantics"""
    
    Tables:
    - kv_store (key, value, expires_at, created_at, updated_at)
    - sessions (session_id, cluster_id, data, expires_at)
    - queues (queue_name, message_id, payload, created_at, locked_by)
    - events (channel, message, created_at)
    
    Features:
    - JSONB for flexible data storage
    - Indexes on key patterns and expiration
    - SKIP LOCKED for queue operations
    - LISTEN/NOTIFY for pub/sub
    - Scheduled cleanup for TTL
```

##### HybridBackend (Optimal)
```python
class HybridBackend(StorageBackend):
    """Best of both worlds approach"""
    
    PostgreSQL for:
    - Sessions (persistent, auditable)
    - Authentication data (ACID compliance)
    - Audit logs and history
    
    Redis for:
    - Active cluster cache (performance)
    - Pub/sub events (real-time)
    - Queue operations (speed)
    - Temporary cache (TTL)
```

### Data Migration Strategy

#### PostgreSQL Schema Design
```sql
-- Core key-value store with TTL support
CREATE TABLE kv_store (
    key VARCHAR(255) PRIMARY KEY,
    value JSONB NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX idx_kv_expires ON kv_store(expires_at) WHERE expires_at IS NOT NULL;

-- Sessions table with optimized lookups
CREATE TABLE sessions (
    session_id UUID PRIMARY KEY,
    cluster_id VARCHAR(255) NOT NULL,
    user_id VARCHAR(255),
    correlation_id VARCHAR(255),
    service_identity VARCHAR(255),
    data JSONB NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_activity TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX idx_sessions_cluster ON sessions(cluster_id);
CREATE INDEX idx_sessions_correlation ON sessions(correlation_id) WHERE correlation_id IS NOT NULL;
CREATE INDEX idx_sessions_expires ON sessions(expires_at);

-- Queue implementation with SKIP LOCKED
CREATE TABLE queues (
    id BIGSERIAL PRIMARY KEY,
    queue_name VARCHAR(255) NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    locked_by VARCHAR(255),
    locked_at TIMESTAMP WITH TIME ZONE,
    processed_at TIMESTAMP WITH TIME ZONE
);
CREATE INDEX idx_queues_name_unprocessed ON queues(queue_name, created_at) 
    WHERE processed_at IS NULL AND locked_by IS NULL;

-- Events for pub/sub via LISTEN/NOTIFY
CREATE TABLE events (
    id BIGSERIAL PRIMARY KEY,
    channel VARCHAR(255) NOT NULL,
    message JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX idx_events_channel_time ON events(channel, created_at DESC);
```

### Implementation Roadmap

#### Step 1: Abstract Current Implementation
1. Create `StorageBackend` abstract base class
2. Implement `RedisBackend` wrapping current code
3. Update all modules to use abstraction
4. Validate no performance regression

#### Step 2: PostgreSQL Backend Development
1. Implement core KV operations
2. Add session management with indexes
3. Implement queue with SKIP LOCKED
4. Add LISTEN/NOTIFY for pub/sub
5. Create TTL cleanup mechanism

#### Step 3: Configuration System
```yaml
# config.yaml example
storage:
  backend: hybrid  # redis | postgresql | hybrid
  
  redis:
    url: redis://localhost:6379/0
    pool_size: 10
    
  postgresql:
    url: postgresql://user:pass@localhost/kubently
    pool_size: 20
    
  hybrid:
    persistent: postgresql  # for sessions, auth
    cache: redis           # for performance-critical ops
    events: redis          # for pub/sub
```

#### Step 4: Migration Tools
```python
# Migration script structure
class StorageMigrator:
    def migrate_redis_to_postgres():
        """One-time migration of existing data"""
        
    def sync_backends():
        """Keep backends in sync during transition"""
        
    def validate_migration():
        """Verify data integrity post-migration"""
```

### Performance Analysis

#### Benchmark Targets
```
Operation           Redis    PostgreSQL   Hybrid
─────────────────────────────────────────────────
Simple GET          <1ms     2-5ms        <1ms
Simple SET          <1ms     3-8ms        3-8ms*
Session Create      2ms      8-15ms       8-15ms
Queue Pop           <1ms     5-10ms       <1ms
Pub/Sub Latency     <1ms     2-5ms        <1ms
Cluster Active      <1ms     2-5ms        <1ms**

* Persistent data goes to PostgreSQL
** Cached in Redis with PostgreSQL fallback
```

#### Optimization Strategies

##### PostgreSQL Performance
1. **Connection Pooling**
   - Use asyncpg with pool_size=20-50
   - Prepared statements for hot paths

2. **Query Optimization**
   - Partial indexes for active sessions
   - BRIN indexes for time-series data
   - Materialized views for analytics

3. **TTL Implementation**
   ```sql
   -- Option 1: Scheduled cleanup job
   DELETE FROM kv_store WHERE expires_at < NOW();
   
   -- Option 2: pg_cron extension
   SELECT cron.schedule('cleanup', '*/5 * * * *', 
     'DELETE FROM kv_store WHERE expires_at < NOW()');
   ```

### Configuration Examples

#### 1. Performance-Critical Setup
```yaml
# Optimized for speed, suitable for development
storage:
  backend: redis
  redis:
    url: redis://localhost:6379/0
    max_connections: 100
```

#### 2. Production-Ready Setup
```yaml
# ACID compliance with reasonable performance
storage:
  backend: postgresql
  postgresql:
    url: postgresql://kubently:pass@db.prod/kubently
    pool_size: 30
    statement_cache_size: 100
  ttl_cleanup_interval: 300  # 5 minutes
```

#### 3. Enterprise Hybrid Setup
```yaml
# Maximum flexibility and performance
storage:
  backend: hybrid
  
  postgresql:
    url: postgresql://kubently:pass@db.prod/kubently
    pool_size: 30
    use_for:
      - sessions
      - authentication
      - audit_logs
      
  redis:
    url: redis://redis.prod:6379/0
    pool_size: 50
    use_for:
      - cache
      - pubsub
      - queues
      - active_clusters
```

### Risk Assessment & Mitigation

#### Technical Risks

1. **Pub/Sub Scalability**
   - Risk: PostgreSQL LISTEN/NOTIFY limited to 8000 byte payloads
   - Mitigation: Store large payloads in table, notify with ID only
   - Alternative: Keep Redis for pub/sub in hybrid mode

2. **Queue Performance**
   - Risk: PostgreSQL queues slower than Redis lists
   - Mitigation: Use SKIP LOCKED, batch processing
   - Alternative: Consider pg_message_queue or keep Redis

3. **TTL Complexity**
   - Risk: No native TTL in PostgreSQL
   - Mitigation: Scheduled cleanup jobs
   - Alternative: Trigger-based cleanup on access

4. **Migration Downtime**
   - Risk: Data migration could cause service interruption
   - Mitigation: Dual-write during transition
   - Alternative: Blue-green deployment with sync

#### Operational Considerations

1. **Monitoring Requirements**
   - Add PostgreSQL-specific metrics
   - Track cleanup job performance
   - Monitor connection pool utilization

2. **Backup Strategy**
   - PostgreSQL: pg_dump, streaming replication
   - Redis: RDB snapshots, AOF
   - Hybrid: Coordinate backup timing

3. **Disaster Recovery**
   - PostgreSQL: Point-in-time recovery
   - Redis: Replica failover
   - Document recovery procedures

### Testing Strategy

#### Unit Tests
```python
@pytest.mark.parametrize("backend", ["redis", "postgresql", "hybrid"])
def test_storage_operations(backend):
    """Test all operations work identically across backends"""
```

#### Performance Tests
```python
def benchmark_storage_backends():
    """Compare operation latencies across backends"""
    operations = ["get", "set", "queue_pop", "publish"]
    backends = ["redis", "postgresql", "hybrid"]
    # Generate comparison matrix
```

#### Integration Tests
1. Test failover scenarios
2. Verify data consistency
3. Load test each backend
4. Test migration tools

### Rollout Plan

#### Phase 1: Foundation (Week 1-2)
- Implement storage abstraction
- Wrap current Redis implementation
- Add configuration system
- Update documentation

#### Phase 2: PostgreSQL Backend (Week 3-4)
- Implement PostgreSQL backend
- Create database schemas
- Add TTL cleanup mechanism
- Write comprehensive tests

#### Phase 3: Hybrid Implementation (Week 5)
- Implement hybrid backend
- Configure routing logic
- Optimize hot paths
- Performance benchmarking

#### Phase 4: Migration Tools (Week 6)
- Create migration scripts
- Build validation tools
- Document procedures
- Test migration scenarios

#### Phase 5: Production Readiness (Week 7-8)
- Load testing
- Security review
- Monitoring setup
- Documentation completion

### Success Criteria

1. **Functional Parity**
   - All existing features work with all backends
   - No breaking changes to APIs

2. **Performance Targets**
   - Redis backend: No regression
   - PostgreSQL: <10ms for critical operations
   - Hybrid: Best of both worlds achieved

3. **Operational Excellence**
   - Zero-downtime migration possible
   - Clear documentation
   - Monitoring and alerting ready

4. **User Experience**
   - Simple configuration
   - Clear migration path
   - Performance trade-offs documented

### Appendix A: Configuration Reference

```yaml
# Complete configuration reference
storage:
  # Backend selection: redis | postgresql | hybrid
  backend: hybrid
  
  # Redis configuration
  redis:
    url: redis://localhost:6379/0
    password: optional_password
    pool_size: 10
    max_connections: 50
    socket_timeout: 5
    socket_connect_timeout: 5
    
  # PostgreSQL configuration
  postgresql:
    url: postgresql://user:pass@host:5432/database
    pool_size: 20
    min_pool_size: 5
    max_pool_size: 30
    command_timeout: 10
    statement_cache_size: 100
    
  # Hybrid mode configuration
  hybrid:
    # Which backend for which data type
    routing:
      sessions: postgresql
      authentication: postgresql
      queues: redis
      cache: redis
      pubsub: redis
      audit: postgresql
    
    # Fallback strategy
    fallback:
      enabled: true
      order: [redis, postgresql]
    
    # Sync strategy for dual writes
    sync:
      enabled: false
      async: true
      
  # TTL cleanup (PostgreSQL only)
  ttl:
    enabled: true
    interval: 300  # seconds
    batch_size: 1000
    
  # Migration settings
  migration:
    dual_write: false
    sync_interval: 60
    validation_enabled: true
```

### Appendix B: Monitoring Metrics

```yaml
# Key metrics to monitor
metrics:
  redis:
    - connection_pool_size
    - operation_latency
    - memory_usage
    - evicted_keys
    
  postgresql:
    - connection_pool_utilization
    - query_execution_time
    - table_sizes
    - cleanup_job_duration
    - deadlock_count
    
  hybrid:
    - backend_routing_decisions
    - fallback_triggers
    - sync_lag
    - cache_hit_ratio
```

### Conclusion

This dual storage backend support plan provides Kubently users with the flexibility to choose the storage solution that best fits their operational requirements. The hybrid approach offers an optimal balance between performance and reliability, while maintaining backward compatibility with existing Redis-only deployments.

The implementation focuses on:
- Clean abstraction without performance penalties
- Gradual migration path
- Comprehensive testing
- Clear documentation
- Operational excellence

This approach ensures that users can confidently deploy Kubently in various environments, from development laptops to enterprise production systems, with storage backends appropriate to their needs.