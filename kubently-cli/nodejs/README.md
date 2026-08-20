# Kubently CLI

[![npm version](https://badge.fury.io/js/@kubently%2Fcli.svg)](https://www.npmjs.com/package/@kubently/cli)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A modern, beautiful Node.js/TypeScript implementation of the Kubently CLI - your interactive Kubernetes debugging companion.

## 🚀 Features

### ✨ Modern User Experience
- **Beautiful Terminal UI**: Colored output, ASCII art banners, and formatted tables
- **Interactive Prompts**: User-friendly input
- **Real-time Feedback**: Loading spinners for all async operations
- **Rich Formatting**: Clear visual hierarchy with chalk styling

### 📦 Administrative Commands
- **Cluster Management**: Add, list, status check, and remove clusters
- **Token Generation**: Automatic agent token creation and management

### 📜 Audit Trail
- **`kubently audit`**: See what the agent actually ran — cluster, time, command, outcome
- **Filters**: By cluster, debug session, and time range (`--since 2h`)
- **Export**: `--output json` / `--output csv` for compliance evidence
- **Scoped**: You read the commands your own API key ran, and nothing else

See [docs/AUDIT.md](../../docs/AUDIT.md) for scoping and retention.

### 🐛 A2A Debug Mode
- **Interactive Terminal**: Real-time chat interface with the Kubently agent
- **Natural Language**: Ask questions in plain English
- **Session Management**: TTL-based sessions with unique IDs
- **Command History**: Navigate previous commands easily

## 📋 Requirements

- Node.js 18.0.0 or higher
- npm or yarn
- Access to a Kubently API server

## 🔧 Installation

### Install from npm (Recommended)

```bash
npm i -g @kubently/cli
```

Or use with npx (no installation required):

```bash
npx @kubently/cli
```

### Install from Source

For development or contributing:

```bash
# Clone the repository
git clone https://github.com/kubently/kubently.git
cd kubently/kubently-cli/nodejs

# Install dependencies
npm install

# Build the TypeScript code
npm run build

# Create global CLI command
npm link
```

## 🚀 Quick Start

### 1. Initialize Configuration

```bash
kubently init
```

This will prompt you for:
- Kubently API URL (e.g., http://localhost:8000)
- Admin API Key

### 2. Start Debug Session

```bash
# Debug specific cluster
kubently debug my-cluster

# Start without cluster (specify in queries)
kubently debug
```

## 📝 Configuration

Configuration is stored in `~/.kubently/config.json` with restrictive permissions (600).

### Environment Variables

```bash
export KUBENTLY_API_URL=http://your-api-url
export KUBENTLY_API_KEY=your-api-key
```

Environment variables take precedence over config file values.

## 🎨 Debug Mode Commands

When in debug mode, you can use:

- `help` - Show available commands
- `clear` - Clear the screen
- `history` - Show command history
- `exit` or `quit` - Exit the session
- Natural language queries (e.g., "What pods are failing?")

## 🛠️ Development

```bash
# Run in development mode
npm run dev

# Run tests
npm test

# Lint code
npm run lint

# Format code
npm run format
```

## 📂 Project Structure

```
nodejs-cli/
├── src/
│   ├── index.ts           # Main entry point
│   ├── commands/          # Command implementations
│   │   ├── init.ts        # Configuration setup
│   │   ├── cluster.ts     # Cluster management
│   │   ├── exec.ts        # Command execution
│   │   ├── audit.ts       # Audit trail listing and export
│   │   └── debug.ts       # A2A debug session
│   └── lib/              # Core libraries
│       ├── config.ts      # Configuration management
│       ├── adminClient.ts # Admin API client
│       ├── a2aClient.ts   # A2A protocol client
│       └── templates.ts   # Manifest generators
├── dist/                  # Compiled JavaScript
├── package.json          # Dependencies and scripts
└── tsconfig.json         # TypeScript configuration
```

## 🤝 Contributing

Contributions are welcome! Please ensure:
- Code is TypeScript compliant
- All tests pass
- Linting rules are followed
- Documentation is updated

## 📄 License

APACHE 2.0

## 🆘 Support

For issues or questions, please file an issue in the repository.

---

Built with ❤️ using Node.js and TypeScript