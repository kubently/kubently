# NPM Publishing Setup Guide

This guide explains how to set up automated npm publishing for the Kubently CLI.

## Prerequisites

1. An npm account with publish permissions for the `@kubently` organization
2. GitHub repository admin access to configure secrets

## Setup Steps

### 1. Create NPM Access Token

1. Log in to [npmjs.com](https://www.npmjs.com/)
2. Click your profile icon → Access Tokens
3. Click "Generate New Token" → "Classic Token"
4. Select "Automation" type (for CI/CD)
5. Copy the token (starts with `npm_...`)

### 2. Add Token to GitHub Secrets

1. Go to your GitHub repository: https://github.com/kubently/kubently
2. Navigate to Settings → Secrets and variables → Actions
3. Click "New repository secret"
4. Name: `NPM_TOKEN`
5. Value: Paste the npm token you copied
6. Click "Add secret"

### 3. Verify npm Organization Access

Make sure the `@kubently` organization exists on npm:

```bash
# Check if you have access
npm access list packages @kubently
```

If the organization doesn't exist, create it at: https://www.npmjs.com/org/create

## Publishing

### Automated Publishing (Recommended)

The package will automatically publish when you create a tag:

```bash
# Update version in package.json first
cd kubently-cli/nodejs
npm version patch  # or minor, major

# Create and push tag
git tag cli-v2.1.3
git push origin cli-v2.1.3
```

The GitHub Action will:
1. Run tests
2. Build the package
3. Publish to npm with provenance
4. Create a GitHub release

### Manual Publishing

If you need to publish manually:

```bash
cd kubently-cli/nodejs

# Login to npm (one-time)
npm login

# Ensure you're on main branch and up to date
git checkout main
git pull

# Run tests
npm test

# Build
npm run build

# Publish
npm publish --access public
```

## Version Management

We use semantic versioning (semver):

- **Patch** (2.1.2 → 2.1.3): Bug fixes
- **Minor** (2.1.3 → 2.2.0): New features (backward compatible)
- **Major** (2.2.0 → 3.0.0): Breaking changes

Update version using npm:

```bash
npm version patch  # 2.1.2 → 2.1.3
npm version minor  # 2.1.2 → 2.2.0
npm version major  # 2.1.2 → 3.0.0
```

## Tag Naming Convention

- CLI tags: `cli-v2.1.3`
- This keeps CLI releases separate from main project releases
- GitHub Actions triggers only on tags matching `cli-v*.*.*`

## Troubleshooting

### Permission Denied

If you get a 403 error:
1. Verify you're a member of the `@kubently` npm organization
2. Check the organization has the package scope enabled
3. Ensure your NPM_TOKEN is valid and has automation permissions

### Build Failures

If the GitHub Action fails:
1. Check the Actions tab: https://github.com/kubently/kubently/actions
2. Review the logs for specific errors
3. Tests must pass before publishing
4. Ensure `dist/` directory is built correctly

### Package Already Exists

If version already published:
1. Update version in package.json
2. Commit the change
3. Create a new tag with the updated version

## Package Details

- **Name**: `@kubently/cli`
- **Registry**: https://registry.npmjs.org/
- **Package URL**: https://www.npmjs.com/package/@kubently/cli
- **Install**: `npm install -g @kubently/cli`

## Security Notes

- Never commit npm tokens to git
- Use automation tokens for CI/CD, not publish tokens
- Enable 2FA on your npm account
- Rotate tokens periodically
- The workflow uses provenance for supply chain security
