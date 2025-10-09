#!/bin/bash

# Build and push Docker images to GitHub Container Registry
# Usage: ./scripts/build-and-push.sh [tag]
# Example: ./scripts/build-and-push.sh v0.0.1
# If no tag provided, uses latest and git commit SHA

set -e

# Configuration
REGISTRY="ghcr.io"
USERNAME="${GITHUB_USERNAME:-kubently}"
REPO_NAME="kubently"
COMMIT_SHA=$(git rev-parse --short HEAD)
BRANCH=$(git branch --show-current)

# Get version tag from argument or use defaults
VERSION_TAG="${1:-}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}Building and pushing Kubently Docker images${NC}"
echo "Registry: ${REGISTRY}"
echo "Username: ${USERNAME}"
echo "Commit: ${COMMIT_SHA}"
echo "Branch: ${BRANCH}"

# Login to GitHub Container Registry
echo -e "\n${YELLOW}Logging in to GitHub Container Registry...${NC}"
echo "${GITHUB_TOKEN}" | docker login ${REGISTRY} -u ${USERNAME} --password-stdin

if [ $? -ne 0 ]; then
    echo -e "${RED}Failed to login to GitHub Container Registry${NC}"
    echo "Please ensure GITHUB_TOKEN environment variable is set with packages:write permission"
    exit 1
fi

# Build API image
echo -e "\n${YELLOW}Building API image...${NC}"
docker build -f deployment/docker/api/Dockerfile \
    -t ${REGISTRY}/${USERNAME}/${REPO_NAME}:latest \
    -t ${REGISTRY}/${USERNAME}/${REPO_NAME}:${BRANCH} \
    -t ${REGISTRY}/${USERNAME}/${REPO_NAME}:sha-${COMMIT_SHA} \
    .

# Add version tag if provided
if [ -n "${VERSION_TAG}" ]; then
    # Remove 'v' prefix if present
    VERSION=${VERSION_TAG#v}
    docker tag ${REGISTRY}/${USERNAME}/${REPO_NAME}:latest \
        ${REGISTRY}/${USERNAME}/${REPO_NAME}:${VERSION}

    # Also tag major and major.minor versions
    MAJOR=$(echo ${VERSION} | cut -d. -f1)
    MINOR=$(echo ${VERSION} | cut -d. -f2)

    docker tag ${REGISTRY}/${USERNAME}/${REPO_NAME}:latest \
        ${REGISTRY}/${USERNAME}/${REPO_NAME}:${MAJOR}
    docker tag ${REGISTRY}/${USERNAME}/${REPO_NAME}:latest \
        ${REGISTRY}/${USERNAME}/${REPO_NAME}:${MAJOR}.${MINOR}
fi

# Build executor image
echo -e "\n${YELLOW}Building executor image...${NC}"
docker build -f deployment/docker/executor/Dockerfile \
    -t ${REGISTRY}/${USERNAME}/${REPO_NAME}-executor:latest \
    -t ${REGISTRY}/${USERNAME}/${REPO_NAME}-executor:${BRANCH} \
    -t ${REGISTRY}/${USERNAME}/${REPO_NAME}-executor:sha-${COMMIT_SHA} \
    .

# Add version tag for executor if provided
if [ -n "${VERSION_TAG}" ]; then
    docker tag ${REGISTRY}/${USERNAME}/${REPO_NAME}-executor:latest \
        ${REGISTRY}/${USERNAME}/${REPO_NAME}-executor:${VERSION}
    docker tag ${REGISTRY}/${USERNAME}/${REPO_NAME}-executor:latest \
        ${REGISTRY}/${USERNAME}/${REPO_NAME}-executor:${MAJOR}
    docker tag ${REGISTRY}/${USERNAME}/${REPO_NAME}-executor:latest \
        ${REGISTRY}/${USERNAME}/${REPO_NAME}-executor:${MAJOR}.${MINOR}
fi

# Push API images
echo -e "\n${YELLOW}Pushing API images...${NC}"
docker push ${REGISTRY}/${USERNAME}/${REPO_NAME}:latest
docker push ${REGISTRY}/${USERNAME}/${REPO_NAME}:${BRANCH}
docker push ${REGISTRY}/${USERNAME}/${REPO_NAME}:sha-${COMMIT_SHA}

if [ -n "${VERSION_TAG}" ]; then
    docker push ${REGISTRY}/${USERNAME}/${REPO_NAME}:${VERSION}
    docker push ${REGISTRY}/${USERNAME}/${REPO_NAME}:${MAJOR}
    docker push ${REGISTRY}/${USERNAME}/${REPO_NAME}:${MAJOR}.${MINOR}
fi

# Push executor images
echo -e "\n${YELLOW}Pushing executor images...${NC}"
docker push ${REGISTRY}/${USERNAME}/${REPO_NAME}-executor:latest
docker push ${REGISTRY}/${USERNAME}/${REPO_NAME}-executor:${BRANCH}
docker push ${REGISTRY}/${USERNAME}/${REPO_NAME}-executor:sha-${COMMIT_SHA}

if [ -n "${VERSION_TAG}" ]; then
    docker push ${REGISTRY}/${USERNAME}/${REPO_NAME}-executor:${VERSION}
    docker push ${REGISTRY}/${USERNAME}/${REPO_NAME}-executor:${MAJOR}
    docker push ${REGISTRY}/${USERNAME}/${REPO_NAME}-executor:${MAJOR}.${MINOR}
fi

echo -e "\n${GREEN}Successfully built and pushed all images!${NC}"
echo -e "\nImages available at:"
echo "  - ${REGISTRY}/${USERNAME}/${REPO_NAME}:latest"
echo "  - ${REGISTRY}/${USERNAME}/${REPO_NAME}:${BRANCH}"
echo "  - ${REGISTRY}/${USERNAME}/${REPO_NAME}:sha-${COMMIT_SHA}"
if [ -n "${VERSION_TAG}" ]; then
    echo "  - ${REGISTRY}/${USERNAME}/${REPO_NAME}:${VERSION}"
    echo "  - ${REGISTRY}/${USERNAME}/${REPO_NAME}:${MAJOR}.${MINOR}"
    echo "  - ${REGISTRY}/${USERNAME}/${REPO_NAME}:${MAJOR}"
fi

echo -e "\nExecutor images:"
echo "  - ${REGISTRY}/${USERNAME}/${REPO_NAME}-executor:latest"
echo "  - ${REGISTRY}/${USERNAME}/${REPO_NAME}-executor:${BRANCH}"
echo "  - ${REGISTRY}/${USERNAME}/${REPO_NAME}-executor:sha-${COMMIT_SHA}"
if [ -n "${VERSION_TAG}" ]; then
    echo "  - ${REGISTRY}/${USERNAME}/${REPO_NAME}-executor:${VERSION}"
    echo "  - ${REGISTRY}/${USERNAME}/${REPO_NAME}-executor:${MAJOR}.${MINOR}"
    echo "  - ${REGISTRY}/${USERNAME}/${REPO_NAME}-executor:${MAJOR}"
fi