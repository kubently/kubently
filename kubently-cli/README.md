# Kubently CLI

Modern Node.js CLI client for Kubently - Interactive Kubernetes Debugging System.

## Installation

### From Source

```bash
cd nodejs
npm install
npm run build
npm link
```

### Quick Update (for development)

```bash
cd nodejs
./update-local.sh
```

Or if you've added the alias to your shell:
```bash
kb-update
```

## Usage

### Interactive Mode
```bash
kubently
```

### Debug Mode
```bash
kubently --api-url http://localhost:8080 --api-key <your-key> debug
```

### Login (OAuth or API Key)
```bash
kubently login
```

### Cluster Management
```bash
kubently cluster list
kubently cluster register <cluster-id>
```

## Features

- 🚀 Interactive debugging sessions with A2A (Agent-to-Agent) protocol
- 🔐 OAuth 2.0 and API key authentication
- 📊 Cluster management and monitoring
- 🎨 Beautiful CLI interface with color output
- ⚡ High-performance Node.js/TypeScript implementation
- 🔄 Real-time streaming responses

## Development

See [nodejs/DEV_SETUP.md](nodejs/DEV_SETUP.md) for detailed development instructions.

### Quick Start

1. Make your changes in `nodejs/src/`
2. Run `./update-local.sh` to rebuild and update
3. Test with `kubently --help`

## Requirements

- Node.js 18+ (20.17.0 recommended)
- npm or yarn
- Kubently API server running

## Documentation

- [Development Setup](nodejs/DEV_SETUP.md)
- [API Documentation](../docs/API.md)
- [Architecture Overview](../docs/ARCHITECTURE.md)

## License

MIT