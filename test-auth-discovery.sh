#!/bin/bash

# Test Authentication Discovery

echo "🔍 Testing Authentication Discovery"
echo "==================================="
echo ""

# Function to test discovery endpoint
test_discovery() {
    local api_url=$1
    echo "Testing discovery at: $api_url/.well-known/kubently-auth"
    
    response=$(curl -s "$api_url/.well-known/kubently-auth" 2>/dev/null)
    
    if echo "$response" | jq . > /dev/null 2>&1; then
        echo "✅ Discovery endpoint returned valid JSON:"
        echo "$response" | jq .
        
        # Check if OAuth is enabled
        oauth_enabled=$(echo "$response" | jq -r '.oauth.enabled')
        if [ "$oauth_enabled" = "true" ]; then
            issuer=$(echo "$response" | jq -r '.oauth.issuer')
            client_id=$(echo "$response" | jq -r '.oauth.client_id')
            echo ""
            echo "📝 OAuth Configuration:"
            echo "   Enabled: Yes"
            echo "   Issuer: $issuer"
            echo "   Client ID: $client_id"
        else
            echo ""
            echo "📝 OAuth Configuration:"
            echo "   Enabled: No"
            echo "   Message: $(echo "$response" | jq -r '.oauth.message')"
        fi
        
        # Show supported methods
        echo ""
        echo "📝 Supported Authentication Methods:"
        echo "$response" | jq -r '.authentication_methods[]' | while read method; do
            echo "   - $method"
        done
    else
        echo "❌ Discovery endpoint not available or returned invalid response"
        echo "Response: $response"
    fi
}

# Test local deployment
echo "1️⃣  Testing Local Deployment (http://localhost:8080)"
echo "------------------------------------------------"
test_discovery "http://localhost:8080"

echo ""
echo ""
echo "2️⃣  Testing CLI Auto-Discovery"
echo "----------------------------"
echo "The CLI will now automatically discover OIDC configuration when running:"
echo ""
echo "  kubently login"
echo ""
echo "It will:"
echo "  1. Query $api_url/.well-known/kubently-auth"
echo "  2. If OAuth is enabled, use the discovered issuer and client_id"
echo "  3. If OAuth is disabled, suggest using API key authentication"
echo "  4. Fall back to environment variables or defaults if discovery fails"
echo ""
echo "You can override discovery with:"
echo "  kubently login --issuer <url> --client-id <id>"
echo "  kubently login --no-discovery  # Skip discovery entirely"
echo ""
echo "==================================="
echo "✅ Discovery Test Complete"
echo "====================================="