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

Source the environment file before deploying:
```bash
source secrets/kubently-tokens.env
```

## Security:

- All files in this folder are gitignored
- Rotate credentials regularly
- Never share these files via email, Slack, etc.
- Use secure methods (1Password, Vault) for team sharing
