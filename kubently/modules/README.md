# Kubently Module Architecture

Following the black box principles from sys-arch-prompt.md, each module is:
- **Self-contained**: Complete functionality within clear boundaries
- **Replaceable**: Can swap implementation without affecting others
- **Single Responsibility**: Does one thing well
- **Interface-driven**: Exposes only what's necessary

## Module Structure

```
kubently/
├── api/                 # HTTP interface layer (orchestration only)
│   ├── main.py         # FastAPI routes - no business logic
│   ├── config.py       # Configuration management
│   └── a2a_server.py   # A2A protocol server setup
│
└── modules/            # Black box modules
    ├── auth/           # Authentication module
    │   └── Interface: verify_agent(), verify_api_key()
    │
    ├── session/        # Session management
    │   └── Interface: create_session(), get_session(), end_session()
    │
    ├── queue/          # Command queue
    │   └── Interface: push_command(), pop_command(), store_result()
    │
    ├── storage/        # Data persistence abstraction
    │   └── Interface: connect(), store(), retrieve()
    │
    ├── api/            # API data models (primitives)
    │   └── models.py   # Command, Session, Result primitives
    │
    ├── a2a/            # Agent-to-agent communication
    │   └── Interface: A2A protocol server on port 8000
    │
    ├── agent/          # Kubernetes cluster agent
    │   └── Interface: Execute kubectl commands
    │
    └── executor/       # Command execution logic
        └── Interface: execute_command(), validate_command()
```

## Key Principles Applied

### 1. Black Box Interfaces
- Each module exposes only its public interface
- Implementation details are completely hidden
- Modules communicate through well-defined contracts

### 2. Replaceable Components
Example replacements without breaking the system:
- `auth`: Switch from API keys to OAuth/JWT
- `storage`: Replace Redis with PostgreSQL or DynamoDB
- `queue`: Use RabbitMQ instead of Redis
- `a2a`: Disable or use different protocol

### 3. Single Responsibility
- `auth`: ONLY handles authentication
- `session`: ONLY manages session lifecycle
- `queue`: ONLY handles command distribution
- Each module has ONE clear job

### 4. Primitive-First Design
Core primitives flow through the system:
- **Command**: kubectl operation to execute
- **Session**: debugging context
- **Result**: command output

All complexity built through composition of these primitives.

## Module Independence

No module imports another module's internals:
- ✅ `from ..modules.auth import AuthModule`
- ❌ `from ..modules.auth.auth import _internal_function`

Modules get dependencies injected:
```python
auth_module = AuthModule(redis_client)  # Injected dependency
session_module = SessionModule(redis_client, ttl=300)
```

## Testing

Each module can be tested in complete isolation:
```python
# Test auth module alone
auth = AuthModule(mock_redis)
assert auth.verify_api_key("test-key")

# Test session module alone  
session = SessionModule(mock_redis)
session_id = await session.create_session("cluster-1")
```

## Benefits

1. **Maintainability**: Any developer can understand one module
2. **Scalability**: Add new modules without touching existing ones
3. **Reliability**: Module failures are isolated
4. **Flexibility**: Swap implementations as needs change
5. **Clarity**: Clear boundaries and responsibilities