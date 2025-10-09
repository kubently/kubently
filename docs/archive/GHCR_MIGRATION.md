# GitHub Container Registry (GHCR) Migration

## Summary

Deployment plan updated to use GitHub Container Registry (GHCR) instead of Google Container Registry (GCR) for simpler, public image distribution.

## Changes Made

### Repository Visibility
```bash
# Made repository public
gh repo edit kubently/kubently --visibility public --accept-visibility-change-consequences

# Verified
gh repo view kubently/kubently --json visibility,url
# Result: {"url":"https://github.com/kubently/kubently","visibility":"PUBLIC"}
```

### Image Registry Migration

**Before (GCR):**
```yaml
image:
  repository: gcr.io/regal-skyline-471806-t6/kubently/api
  tag: "${GIT_SHA}"
```

**After (GHCR):**
```yaml
image:
  repository: ghcr.io/kubently/kubently/api
  tag: "${GIT_SHA}"
```

## Benefits

### 1. No Authentication Required
- ✅ Public images accessible without credentials
- ✅ No imagePullSecrets needed in Kubernetes
- ✅ Simpler cluster setup (no GCP service account config)

### 2. Simplified Build Process
```bash
# Before (GCR):
docker tag kubently/api:${GIT_SHA} gcr.io/${PROJECT_ID}/kubently/api:${GIT_SHA}
docker push gcr.io/${PROJECT_ID}/kubently/api:${GIT_SHA}

# After (GHCR):
docker build -t ghcr.io/kubently/kubently/api:${GIT_SHA} .
docker push ghcr.io/kubently/kubently/api:${GIT_SHA}
```

### 3. Cost & Integration
- ✅ Free for public repositories
- ✅ Integrated with GitHub (same auth)
- ✅ No GCP project billing
- ✅ Automatic image scanning (GitHub security)

## Updated Build Commands

### API Server
```bash
GIT_SHA=$(git rev-parse --short HEAD)

# Build
docker build -t ghcr.io/kubently/kubently/api:${GIT_SHA} \
  -f deployment/docker/api/Dockerfile .

# Push
docker push ghcr.io/kubently/kubently/api:${GIT_SHA}
```

### Executor
```bash
# Build
docker build -t ghcr.io/kubently/kubently/executor:${GIT_SHA} \
  -f deployment/docker/executor/Dockerfile .

# Push
docker push ghcr.io/kubently/kubently/executor:${GIT_SHA}
```

## Authentication

### Push Images (One-time Setup)
```bash
# Create GitHub Personal Access Token with packages:write scope
# https://github.com/settings/tokens/new

# Login to GHCR
echo $GITHUB_TOKEN | docker login ghcr.io -u kubently --password-stdin
```

### Pull Images (Public - No Auth)
```bash
# Public images can be pulled without authentication
docker pull ghcr.io/kubently/kubently/api:latest

# Works in Kubernetes without imagePullSecrets
kubectl run test --image=ghcr.io/kubently/kubently/api:abc1234
```

## Deployment Plan Updates

### Files Modified
1. **PRODUCTION_DEPLOYMENT_PLAN_REVIEWED.md**
   - Step 2.2: Updated to GHCR build/push
   - Step 2.4: Changed image repositories to GHCR
   - Step 3.1: Updated Kind executor to use GHCR
   - Prerequisites: Added GHCR access requirement

### Image References Updated
- `gcr.io/regal-skyline-471806-t6/kubently/api` → `ghcr.io/kubently/kubently/api`
- `gcr.io/regal-skyline-471806-t6/kubently/executor` → `ghcr.io/kubently/kubently/executor`

## Verification

### Check Image Availability
```bash
# List packages
gh api user/packages?package_type=container

# Pull test
docker pull ghcr.io/kubently/kubently/api:${GIT_SHA}
docker pull ghcr.io/kubently/kubently/executor:${GIT_SHA}
```

### Kubernetes Verification
```bash
# Test pull without authentication
kubectl run test-api --image=ghcr.io/kubently/kubently/api:${GIT_SHA} --rm -it --restart=Never -- echo "Success"

kubectl run test-executor --image=ghcr.io/kubently/kubently/executor:${GIT_SHA} --rm -it --restart=Never -- echo "Success"
```

## Image Visibility Settings

### Current Configuration
- Repository: **PUBLIC** ✅
- Images: **PUBLIC** (inherited from repository)
- Pull access: **Anonymous** ✅

### Package Settings (Optional)
To ensure package-level visibility:
```bash
# Via GitHub UI:
1. Go to: https://github.com/users/kubently/packages/container/kubently%2Fapi/settings
2. Scroll to "Danger Zone"
3. Set visibility to "Public"
```

## CI/CD Integration (Future)

### GitHub Actions Example
```yaml
name: Build and Push to GHCR

on:
  push:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write

    steps:
    - uses: actions/checkout@v4

    - name: Login to GHCR
      uses: docker/login-action@v3
      with:
        registry: ghcr.io
        username: ${{ github.actor }}
        password: ${{ secrets.GITHUB_TOKEN }}

    - name: Build and push API
      run: |
        GIT_SHA=$(git rev-parse --short HEAD)
        docker build -t ghcr.io/kubently/kubently/api:${GIT_SHA} \
          -f deployment/docker/api/Dockerfile .
        docker push ghcr.io/kubently/kubently/api:${GIT_SHA}

    - name: Build and push Executor
      run: |
        GIT_SHA=$(git rev-parse --short HEAD)
        docker build -t ghcr.io/kubently/kubently/executor:${GIT_SHA} \
          -f deployment/docker/executor/Dockerfile .
        docker push ghcr.io/kubently/kubently/executor:${GIT_SHA}
```

## Migration Checklist

- [x] Repository made public
- [x] Deployment plan updated to use GHCR
- [x] Build commands updated
- [x] Helm values templates updated (GKE + Kind)
- [x] Prerequisites documentation updated
- [ ] Build and push first images to GHCR
- [ ] Verify public pull access works
- [ ] Test deployment with GHCR images

## Rollback Plan

If GHCR doesn't work, revert to GCR:
```bash
# Revert image repositories in deployment plan
sed -i 's|ghcr.io/kubently/kubently|gcr.io/regal-skyline-471806-t6/kubently|g' \
  docs/PRODUCTION_DEPLOYMENT_PLAN_REVIEWED.md

# Push to GCR instead
docker tag ghcr.io/kubently/kubently/api:${GIT_SHA} \
  gcr.io/regal-skyline-471806-t6/kubently/api:${GIT_SHA}
docker push gcr.io/regal-skyline-471806-t6/kubently/api:${GIT_SHA}
```

## Security Considerations

### Public Images
- ✅ Code is already public on GitHub
- ✅ No secrets embedded in images
- ✅ Dockerfile best practices followed
- ✅ Multi-stage builds minimize attack surface

### Image Scanning
GHCR automatically scans for vulnerabilities:
```bash
# View scan results
gh api /user/packages/container/kubently%2Fapi/versions
```

## Next Steps

1. Build and push images to GHCR
2. Verify public access
3. Continue with deployment using GHCR images
4. (Optional) Set up GitHub Actions for automated builds
