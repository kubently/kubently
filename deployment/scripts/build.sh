#!/bin/bash
# Build script for Kubently Docker images

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
VERSION=${1:-latest}
REGISTRY=${2:-kubently}
PUSH=${3:-false}

echo -e "${GREEN}Building Kubently images version ${VERSION}...${NC}"

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$SCRIPT_DIR/../.."

cd "$PROJECT_ROOT"

# Build API image
echo -e "${YELLOW}Building API image...${NC}"
docker build \
  -t ${REGISTRY}/api:${VERSION} \
  -t ${REGISTRY}/api:latest \
  -f deployment/docker/api/Dockerfile \
  .

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ API image built successfully${NC}"
else
    echo -e "${RED}✗ API image build failed${NC}"
    exit 1
fi

# Build Executor image
echo -e "${YELLOW}Building Executor image...${NC}"
docker build \
  -t ${REGISTRY}/executor:${VERSION} \
  -t ${REGISTRY}/executor:latest \
  -f deployment/docker/executor/Dockerfile \
  .

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Executor image built successfully${NC}"
else
    echo -e "${RED}✗ Executor image build failed${NC}"
    exit 1
fi

# Push images if requested
if [ "$PUSH" == "true" ]; then
    echo -e "${YELLOW}Pushing images to registry...${NC}"
    
    docker push ${REGISTRY}/api:${VERSION}
    docker push ${REGISTRY}/api:latest
    docker push ${REGISTRY}/executor:${VERSION}
    docker push ${REGISTRY}/executor:latest
    
    echo -e "${GREEN}✓ Images pushed successfully${NC}"
fi

echo -e "${GREEN}Build complete!${NC}"
echo ""
echo "Images built:"
echo "  - ${REGISTRY}/api:${VERSION}"
echo "  - ${REGISTRY}/executor:${VERSION}"