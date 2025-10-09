#!/bin/bash

# Kubently CLI Development Helpers
# Source this file: source dev-helpers.sh

# Quick update and rebuild
kb-update() {
    echo "🔄 Quick updating Kubently CLI..."
    npm run rebuild && npm run link:refresh
    echo "✅ Done! Version: $(kubently --version)"
}

# Full update (clean install)
kb-full-update() {
    echo "🔄 Full update of Kubently CLI..."
    ./update-local.sh
}

# Watch mode - rebuilds on file changes
kb-watch() {
    echo "👁️  Starting watch mode (Ctrl+C to stop)..."
    npm run watch
}

# Quick test with local server
kb-test() {
    kubently --api-url http://localhost:8080 --api-key test-api-key debug
}

# Test with custom URL
kb-test-url() {
    local url="${1:-http://localhost:8080}"
    kubently --api-url "$url" --api-key test-api-key debug
}

# Check current version
kb-version() {
    echo "📦 Package version: $(node -p "require('./package.json').version")"
    echo "🔧 Installed version: $(kubently --version)"
    echo "📍 Location: $(which kubently)"
}

# Show available commands
kb-help() {
    echo "🚀 Kubently CLI Development Helpers"
    echo ""
    echo "Available commands:"
    echo "  kb-update       - Quick rebuild and update"
    echo "  kb-full-update  - Full clean install and update"
    echo "  kb-watch        - Watch mode (auto-rebuild on changes)"
    echo "  kb-test         - Test with local server (localhost:8080)"
    echo "  kb-test-url URL - Test with custom URL"
    echo "  kb-version      - Show version info"
    echo "  kb-help         - Show this help"
    echo ""
    echo "Regular npm scripts:"
    echo "  npm run build        - Build TypeScript"
    echo "  npm run clean        - Clean dist directory"
    echo "  npm run rebuild      - Clean and build"
    echo "  npm run update-local - Run full update script"
    echo "  npm run dev:debug    - Build and run debug mode"
}

echo "✅ Kubently dev helpers loaded! Type 'kb-help' for commands."