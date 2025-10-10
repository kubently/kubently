# Docker Build and Push Guide

## Overview

Docker images are built and pushed to GitHub Container Registry (ghcr.io) in two ways:
1. **Automated**: On version tag pushes (v*.*.*)
2. **Manual**: Using the local build script

## Automated Builds (CI/CD)

Images are automatically built and published when you create a version tag:

```bash
git tag v0.0.1
git push origin v0.0.1
```

This triggers GitHub Actions to build multi-platform images (linux/amd64, linux/arm64) with tags:
- `0.0.1` (full version)
- `0.0` (major.minor)
- `0` (major only)
- `latest`
- `sha-<commit>`

## Local Build and Push

For development or quick releases, use the local build script:

### Prerequisites

1. Set GitHub token with packages:write permission:
```bash
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
```

2. Optionally set username (defaults to kubently):
```bash
export GITHUB_USERNAME=your-username
```

### Building and Pushing

```bash
# Build and push with latest + branch + sha tags
./scripts/build-and-push.sh

# Build and push with version tags
./scripts/build-and-push.sh v0.0.2
```

The script will:
1. Build both API and executor images
2. Tag with multiple variants (latest, branch, sha, version)
3. Push all tags to ghcr.io

### Example Output

```
Images available at:
  - ghcr.io/kubently/kubently:latest
  - ghcr.io/kubently/kubently:main
  - ghcr.io/kubently/kubently:sha-abc123
  - ghcr.io/kubently/kubently:0.0.2
  - ghcr.io/kubently/kubently:0.0
  - ghcr.io/kubently/kubently:0

Executor images:
  - ghcr.io/kubently/kubently-executor:latest
  - ghcr.io/kubently/kubently-executor:main
  - ghcr.io/kubently/kubently-executor:sha-abc123
  - ghcr.io/kubently/kubently-executor:0.0.2
  - ghcr.io/kubently/kubently-executor:0.0
  - ghcr.io/kubently/kubently-executor:0
```

## Making Images Public

After first push, make packages public:
1. Go to GitHub → Settings → Packages
2. Click on each package
3. Change visibility to Public

## Using Images

```bash
# Pull latest
docker pull ghcr.io/kubently/kubently:latest

# Pull specific version
docker pull ghcr.io/kubently/kubently:0.0.1

# Use in Kubernetes
kubectl set image deployment/kubently-api api=ghcr.io/kubently/kubently:0.0.2
```

## GitHub Token Setup

Create a Personal Access Token:
1. GitHub → Settings → Developer settings → Personal access tokens
2. Generate new token (classic)
3. Select scopes: `write:packages`, `delete:packages` (optional)
4. Save token securely

## Troubleshooting

**Login fails**: Check GITHUB_TOKEN has packages:write permission
**Build fails**: Ensure Docker daemon is running
**Push fails**: Verify you have push access to the repository
**Multi-platform issues**: Local builds are single-platform; use CI for multi-platform