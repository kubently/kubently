# GitHub Container Registry (ghcr.io) Setup Guide

## Automatic Image Publishing

The GitHub Actions workflow automatically publishes Docker images to ghcr.io when:
- Code is pushed to the `main` branch (tagged as `latest`)
- A version tag is pushed (e.g., `v0.0.1`, `v1.2.3`)
- Pull requests are created (tagged with PR number)

## Versioning Strategy

When you push a tag like `v0.0.1`, the workflow creates multiple tags:
- `0.0.1` - Full version
- `0.0` - Major.minor version
- `0` - Major version only
- `latest` - Only on main branch
- `sha-<commit>` - Commit SHA reference

## How to Trigger a Versioned Release

```bash
# Create and push a version tag
git tag v0.0.1
git push origin v0.0.1

# Or create an annotated tag with a message
git tag -a v0.0.1 -m "Initial release"
git push origin v0.0.1
```

## Making Packages Publicly Accessible

By default, packages are private. To make them public:

### Method 1: Via GitHub UI (Recommended)
1. Go to your repository on GitHub
2. Click on "Packages" in the right sidebar
3. Click on the package you want to make public (e.g., `kubently`)
4. Click "Package settings" (gear icon)
5. Scroll to "Danger Zone"
6. Click "Change visibility"
7. Select "Public" and confirm

### Method 2: During First Push
After the first workflow run:
1. Navigate to: `https://github.com/users/<USERNAME>/packages/container/<PACKAGE_NAME>/settings`
2. Change visibility to "Public"

## Pulling Images

Once public, images can be pulled without authentication:

```bash
# Pull latest version
docker pull ghcr.io/<username>/kubently:latest

# Pull specific version
docker pull ghcr.io/<username>/kubently:0.0.1

# Pull executor image
docker pull ghcr.io/<username>/kubently-executor:latest
```

## Using in Kubernetes/EKS

Update your Kubernetes manifests to use the ghcr.io images:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kubently
spec:
  template:
    spec:
      containers:
      - name: kubently
        image: ghcr.io/<username>/kubently:latest
        # Or use a specific version
        # image: ghcr.io/<username>/kubently:0.0.1
```

## Images Published

The workflow publishes two images:
1. **Main API application**: `ghcr.io/<username>/kubently`
2. **Executor service**: `ghcr.io/<username>/kubently-executor`

## Workflow Features

- **Multi-platform builds**: Supports both `linux/amd64` and `linux/arm64`
- **Build caching**: Uses GitHub Actions cache for faster builds
- **Automatic tagging**: Handles semantic versioning automatically
- **PR previews**: Creates temporary tags for pull requests

## Monitoring Builds

Check workflow status at:
`https://github.com/<username>/kubently/actions/workflows/publish-docker.yml`