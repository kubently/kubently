# Secrets Folder

This folder contains sensitive credentials and tokens for local development and testing.

**⚠️ NEVER commit these files to git!**

## Files in this folder:

- `kubently-tokens.env` - Generated tokens for Kubently API and executors
- `google-api-key.txt` - Google Gemini API key
- `anthropic-api-key.txt` - Anthropic Claude API key
- `openai-api-key.txt` - OpenAI API key
- `certbot/` - TLS certificates from Let's Encrypt (manual)
- `gcp-keys/` - GCP service account keys (if needed)

## Usage:

### Automated (Recommended):

Run the deployment script from the secrets directory - it automatically loads tokens:
```bash
./secrets/deploy-test.sh
```

The script will:
1. Load tokens from `kubently-tokens.env`
2. Pass them to Helm via `--set` parameters
3. Configure Redis with the executor token

### Manual:

If deploying manually, source the environment file first:
```bash
source secrets/kubently-tokens.env

# Then deploy with Helm
helm upgrade --install kubently ./deployment/helm/kubently \
  --set-string "api.apiKeys={$API_KEY,$KIND_EXECUTOR_TOKEN}" \
  --set executor.token="$KIND_EXECUTOR_TOKEN" \
  -f deployment/helm/test-values.yaml
```

## Security:

- All files in this folder are gitignored
- Rotate credentials regularly
- Never share these files via email, Slack, etc.
- Use secure methods (1Password, Vault) for team sharing
