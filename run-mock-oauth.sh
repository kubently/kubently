#!/bin/bash

# Script to run the mock OAuth provider for testing

set -e

echo "🔐 Starting Mock OAuth Provider..."
echo "================================"
echo ""

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is required but not installed"
    exit 1
fi

# Install dependencies if needed
echo "📦 Checking dependencies..."
pip3 install -q PyJWT cryptography fastapi uvicorn

# Start the mock OAuth provider
echo "🚀 Starting Mock OAuth Provider on http://localhost:9000"
echo ""
echo "Available endpoints:"
echo "  - Discovery: http://localhost:9000/.well-known/openid-configuration"
echo "  - JWKS:      http://localhost:9000/jwks"
echo "  - Device:    http://localhost:9000/device"
echo ""
echo "Test users:"
echo "  - test@example.com (regular user)"
echo "  - admin@example.com (admin user)"
echo ""
echo "Press Ctrl+C to stop the server"
echo "================================"
echo ""

# Run the mock provider
python3 kubently/modules/auth/mock_oauth_provider.py