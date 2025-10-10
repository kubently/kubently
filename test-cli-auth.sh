#!/bin/bash

# Test CLI authentication commands

echo "🧪 Testing Kubently CLI Authentication"
echo "====================================="
echo ""

# Test 1: API Key Authentication
echo "1️⃣  Testing API key authentication..."
kubently login --use-api-key test-api-key
echo ""

# Check config was saved
CONFIG_FILE="$HOME/.kubently/config.json"
if [ -f "$CONFIG_FILE" ]; then
    echo "✅ Config file created at: $CONFIG_FILE"
    
    # Check auth method
    AUTH_METHOD=$(grep -o '"authMethod":"[^"]*"' "$CONFIG_FILE" | cut -d'"' -f4)
    if [ "$AUTH_METHOD" = "api_key" ]; then
        echo "✅ Auth method set to: api_key"
    else
        echo "❌ Auth method incorrect: $AUTH_METHOD"
    fi
    
    # Check API key
    if grep -q '"apiKey":"test-api-key"' "$CONFIG_FILE"; then
        echo "✅ API key saved correctly"
    else
        echo "❌ API key not saved correctly"
    fi
else
    echo "❌ Config file not created"
fi

echo ""
echo "2️⃣  Testing OAuth flow (without mock provider)..."
echo "Since the mock provider requires additional setup,"
echo "you can test OAuth manually by:"
echo ""
echo "  1. Install Python dependencies in a venv:"
echo "     python3 -m venv /tmp/oauth-test"
echo "     source /tmp/oauth-test/bin/activate"
echo "     pip install PyJWT cryptography fastapi uvicorn httpx"
echo ""
echo "  2. Run the mock provider:"
echo "     python3 kubently/modules/auth/mock_oauth_provider.py"
echo ""
echo "  3. In another terminal, run:"
echo "     kubently login"
echo ""
echo "  4. Follow the device authorization flow"

echo ""
echo "====================================="
echo "✅ CLI Authentication Test Complete"
echo "====================================="