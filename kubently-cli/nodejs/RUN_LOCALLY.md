# Running Kubently Node.js CLI Locally

This guide shows how to run the Node.js version of Kubently CLI without interfering with your existing Python `kubently` command.

## ✅ VERIFIED WORKING SOLUTION

The debug command has been fixed and tested. It now properly maintains an interactive session.

## Prerequisites

```bash
cd nodejs-cli
npm install
npm run build
```

## Method 1: Direct Node Execution (Recommended)

This is the most reliable way to run the CLI, especially for the interactive debug mode:

```bash
# Using node directly
node dist/index.js --help
node dist/index.js cluster list
node dist/index.js debug

# Or with environment variables
export KUBENTLY_API_URL=http://localhost:8000
export KUBENTLY_API_KEY=your-api-key
node dist/index.js cluster list
```

## Method 2: Using the Dev Script

We've included a special script that handles TTY properly:

```bash
# Make it executable (first time only)
chmod +x kubently-dev

# Run commands
./kubently-dev --help
./kubently-dev cluster list
./kubently-dev debug  # Works perfectly with interactive mode!
```

## Method 3: Shell Alias

Add to your `~/.bashrc` or `~/.zshrc`:

```bash
alias kubently-node="node /Users/adickinson/repos/kubently-nodejs-cli/nodejs-cli/dist/index.js"
```

Then:
```bash
source ~/.bashrc  # or source ~/.zshrc
kubently-node cluster list
kubently-node debug
```

## Method 4: NPM Scripts

For non-interactive commands:

```bash
npm run start -- --help
npm run start -- cluster list
npm run start -- cluster status kind
```

⚠️ **Note**: For the interactive `debug` command, use Method 1 or 2 instead of npm scripts.

## Environment Variables

Set these before running any commands:

```bash
export KUBENTLY_API_URL=http://localhost:8000
export KUBENTLY_API_KEY=your-api-key
```

Or pass them inline:

```bash
KUBENTLY_API_URL=http://localhost:8000 KUBENTLY_API_KEY=key123 node dist/index.js cluster list
```

## Quick Test

Here's a complete example:

```bash
# Build the project
npm run build

# Set credentials
export KUBENTLY_API_URL=http://localhost:8000
export KUBENTLY_API_KEY=testkey123

# Test commands
node dist/index.js version
node dist/index.js cluster list
node dist/index.js cluster status kind
node dist/index.js exec get pods --cluster kind

# Interactive debug session
node dist/index.js debug
# OR
./kubently-dev debug
```

## Troubleshooting

### Debug mode exits immediately

If the debug command exits immediately, you're likely using npm scripts or a non-TTY environment. Use one of these instead:

```bash
# Good - direct node execution
node dist/index.js debug

# Good - dev script
./kubently-dev debug

# Bad - npm scripts don't handle TTY well
npm run start -- debug  # Won't work properly!
```

### Command not found

Make sure you're in the `nodejs-cli` directory and have built the project:

```bash
cd nodejs-cli
npm install
npm run build
```

### API connection errors

Verify your API server is running and credentials are correct:

```bash
# Test with curl
curl http://localhost:8000/health

# Check environment variables
echo $KUBENTLY_API_URL
echo $KUBENTLY_API_KEY
```

## Development Mode

For development with TypeScript hot-reload:

```bash
# Install ts-node if needed
npm install -D ts-node

# Run TypeScript directly
npx ts-node src/index.ts cluster list

# Or use the dev script which auto-detects
./kubently-dev cluster list
```

## Summary

- Your existing Python `kubently` command remains untouched
- Use `node dist/index.js` for the most reliable experience
- The interactive debug mode requires proper TTY handling
- Environment variables or config file can store credentials