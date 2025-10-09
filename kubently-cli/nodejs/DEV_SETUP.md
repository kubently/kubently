# Kubently CLI Development Setup

## Quick Start

After making changes to the CLI code, run:

```bash
./update-local.sh
```

This will rebuild and update your global `kubently` command.

## Development Workflow

### Option 1: Use the Update Script (Recommended)

```bash
# After making changes, run:
./update-local.sh

# Or use npm:
npm run update-local
```

### Option 2: Use Development Helpers

Source the helper functions for quick commands:

```bash
# Load the helpers
source dev-helpers.sh

# Quick rebuild and update
kb-update

# Full clean update
kb-full-update

# Watch mode (auto-rebuild on file changes)
kb-watch

# Test with local server
kb-test

# Check versions
kb-version
```

### Option 3: Manual Steps

```bash
# 1. Clean old build
npm run clean

# 2. Build TypeScript
npm run build

# 3. Update global link
npm link

# 4. Refresh asdf shims (if using asdf)
asdf reshim nodejs
```

## Available NPM Scripts

| Script | Description |
|--------|-------------|
| `npm run build` | Compile TypeScript to JavaScript |
| `npm run clean` | Remove dist directory |
| `npm run rebuild` | Clean and build in one command |
| `npm run update-local` | Full update (clean, build, link) |
| `npm run watch` | Watch mode - auto-rebuild on changes |
| `npm run dev:debug` | Build and run debug mode with test server |
| `npm run link:refresh` | Refresh npm link and asdf shims |

## Troubleshooting

### Command Not Updated

If `kubently` doesn't reflect your changes:

1. **Clear command cache:**
   ```bash
   hash -r
   ```

2. **Check which kubently is being used:**
   ```bash
   which kubently
   kubently --version
   ```

3. **Manually refresh asdf:**
   ```bash
   asdf reshim nodejs
   ```

4. **Force reinstall:**
   ```bash
   npm unlink -g kubently-cli
   npm link
   asdf reshim nodejs
   ```

### Version Mismatch

If the installed version doesn't match package.json:

```bash
# Check both versions
node -p "require('./package.json').version"  # Package version
kubently --version                            # Installed version

# Force update
./update-local.sh
```

### ESM Module Errors

If you see errors about ES modules:

1. Ensure all imports have `.js` extensions
2. Check that `package.json` has `"type": "module"`
3. Rebuild with `npm run rebuild`

## Development Tips

### Quick Iteration

For rapid development:

```bash
# Terminal 1: Watch mode
npm run watch

# Terminal 2: Load helpers and test
source dev-helpers.sh
kb-test  # Run after each auto-build
```

### Testing Changes

```bash
# Test with mock server
node test-mock-server.cjs &  # Start mock server
kubently --api-url http://localhost:8080 --api-key test debug

# Or use the npm script
npm run dev:debug
```

### Before Committing

Always run these before committing:

```bash
# Update version in package.json if needed
# Then:
npm run rebuild
npm run update-local
kubently --version  # Verify it works
```

## Adding Aliases (Optional)

Add to your `~/.zshrc` or `~/.bashrc`:

```bash
# Kubently development
alias kb-update="cd ~/repos/kubently/kubently-cli/nodejs && ./update-local.sh"
alias kb-dev="cd ~/repos/kubently/kubently-cli/nodejs && source dev-helpers.sh"
```

Then you can update from anywhere:

```bash
kb-update  # Updates kubently CLI from any directory
kb-dev     # Jump to CLI directory and load helpers
```