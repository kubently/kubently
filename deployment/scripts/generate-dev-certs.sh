#!/bin/bash
# deployment/scripts/generate-dev-certs.sh
# Generate self-signed certificates for local development

set -e

CERT_DIR="$(dirname "$0")/../dev-certs"
mkdir -p "$CERT_DIR"

echo "🔐 Generating development certificates..."

# Generate self-signed certificate for local development
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "$CERT_DIR/kubently.key" \
    -out "$CERT_DIR/kubently.crt" \
    -subj "/CN=localhost/O=Kubently Development" \
    -addext "subjectAltName=DNS:localhost,DNS:nginx,IP:127.0.0.1"

# Set appropriate permissions
chmod 600 "$CERT_DIR/kubently.key"
chmod 644 "$CERT_DIR/kubently.crt"

echo "✅ Development certificates generated in $CERT_DIR"
echo "⚠️  These are self-signed certificates for LOCAL DEVELOPMENT ONLY"
echo ""
echo "Files created:"
echo "  - $CERT_DIR/kubently.crt (certificate)"
echo "  - $CERT_DIR/kubently.key (private key)"
echo ""
echo "To start development with TLS:"
echo "  cd deployment && docker-compose up"
echo ""
echo "Access your development API at:"
echo "  https://localhost"