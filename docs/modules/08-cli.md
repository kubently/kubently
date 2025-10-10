# Module: CLI Tool with A2A Support

## Overview

The CLI module provides a command-line interface for managing Kubently deployments. It acts as a client to the Kubently API for administrative tasks and uses the A2A (Agent-to-Agent) protocol for interactive debugging sessions. The CLI makes Kubently feel like a production-ready DevOps tool that integrates seamlessly with the A2A ecosystem.

## Black Box Interface

**Purpose**: Command-line interface for Kubently management and A2A debugging

**What this module does** (Public Interface):

- Registers new clusters with the API (Direct API)
- Generates agent deployment manifests (Direct API)
- Manages API keys and access (Direct API)
- Provides status information (Direct API)
- Starts interactive debugging sessions (A2A Protocol)
- Supports OAuth 2.0 authentication flow
- Provides interactive menu-driven interface

**What this module hides** (Implementation):

- HTTP client details for admin operations
- WebSocket/A2A protocol implementation
- Configuration file format
- Token generation logic
- Manifest templating
- OAuth device flow handling

## Dependencies

- Node.js 18+ (20.17.0 recommended)
- TypeScript 5.0+
- Commander.js (CLI framework)
- Axios (HTTP client for admin operations)
- WebSocket (WebSocket client for A2A)
- Chalk (Terminal styling)
- Inquirer (Interactive prompts)

## Implementation Requirements

### File Structure

```sh
kubently-cli/nodejs/
├── src/
│   ├── index.ts          # Main CLI entry point
│   ├── commands/         # CLI commands
│   │   ├── init.ts       # Initialize configuration
│   │   ├── cluster.ts    # Cluster management
│   │   ├── debug.ts      # Debug sessions (A2A)
│   │   ├── login.ts      # Authentication
│   │   └── interactive.ts # Interactive mode
│   ├── lib/
│   │   ├── config.ts     # Configuration management
│   │   ├── a2aClient.ts  # A2A protocol client
│   │   ├── adminClient.ts # Admin API client
│   │   └── templates.ts  # K8s manifest templates
│   └── auth/
│       ├── LoginController.ts      # Login orchestration
│       ├── OAuthDeviceFlowClient.ts # OAuth implementation
│       └── CliAuthUI.ts            # Authentication UI
├── dist/                 # Compiled JavaScript
├── package.json          # Package configuration
├── tsconfig.json         # TypeScript configuration
├── update-local.sh       # Development update script
└── README.md            # CLI documentation
```

### Configuration

The CLI stores configuration in `~/.kubently/config.json`:

```json
{
  "api_url": "http://kubently-api.kubently.svc.cluster.local:8080",
  "api_key": "your-api-key",
  "cluster_id": "default-cluster",
  "auth_method": "oauth|api_key",
  "oauth_tokens": {
    "access_token": "...",
    "refresh_token": "...",
    "expires_at": 1234567890
  }
}
```

### Commands

#### Admin Commands (Direct API)

These commands interact directly with the Kubently Admin API:

```bash
# Initialize configuration
kubently init

# Cluster management
kubently cluster list
kubently cluster register <cluster-id>
kubently cluster delete <cluster-id>
kubently cluster generate-manifest <cluster-id>

# Authentication
kubently login                    # OAuth flow
kubently login --api-key <key>    # API key auth
```

#### Debug Commands (A2A Protocol)

These commands use the A2A protocol for agent interaction:

```bash
# Start interactive debug session
kubently debug [cluster-id]

# Interactive mode with menu
kubently --api-url <url> --api-key <key>
```

### A2A Protocol Integration

The CLI acts as an A2A client when in debug mode:

```typescript
// A2A message format
interface A2AMessage {
  messageId: string;
  role: 'user' | 'assistant';
  parts: Array<{
    text?: string;
    root?: { text: string };
  }>;
  contextId?: string;
}

// A2A request format
interface A2ARequest {
  jsonrpc: '2.0';
  id: string;
  method: 'message/send';
  params: {
    message: A2AMessage;
  };
}
```

### Command Examples

```bash
# First time setup
kubently init
> Enter API URL: http://localhost:8080
> Enter API Key: abc123
> Enter default cluster ID: my-cluster

# Register a new cluster
kubently cluster register production-cluster

# Generate deployment manifest
kubently cluster generate-manifest production-cluster > agent.yaml
kubectl apply -f agent.yaml

# Start debug session
kubently debug
> What pods are running in default namespace?
> Show me the logs for pod nginx
> Check the deployment status

# OAuth login
kubently login
> Visit: https://auth.kubently.com/device
> Enter code: ABCD-1234
> ✓ Authentication successful!
```

### Development

For development setup and workflow:

1. **Install dependencies**:
   ```bash
   cd kubently-cli/nodejs
   npm install
   ```

2. **Build TypeScript**:
   ```bash
   npm run build
   ```

3. **Update local installation**:
   ```bash
   ./update-local.sh
   ```

4. **Watch mode for development**:
   ```bash
   npm run watch
   ```

### Testing

```bash
# Run tests
npm test

# Test with mock server
node test-mock-server.cjs &
kubently --api-url http://localhost:8080 --api-key test debug
```

### Building and Distribution

The CLI can be distributed as:

1. **npm package**: Published to npm registry
2. **Standalone executable**: Using pkg or nexe
3. **Docker image**: For containerized environments

### Error Handling

The CLI provides clear error messages:

```bash
✗ API URL is required.
  Run "kubently init" or set environment variables.

✗ Failed to connect to API server
  Error: ECONNREFUSED http://localhost:8080

✗ Authentication failed
  Invalid API key or expired token
```

### Environment Variables

The CLI supports environment variables for CI/CD:

```bash
export KUBENTLY_API_URL=http://localhost:8080
export KUBENTLY_API_KEY=your-api-key
export KUBENTLY_CLUSTER_ID=default

kubently debug  # Uses env vars
```