# Security Policy

## Supported Versions

We release patches for security vulnerabilities. Currently supported versions:

| Version | Supported          |
| ------- | ------------------ |
| 0.x.x   | :white_check_mark: |

## Reporting a Vulnerability

We take the security of Kubently seriously. If you believe you have found a security vulnerability, please report it to us as described below.

### Please do NOT:
- Open a public GitHub issue for security vulnerabilities
- Post about the vulnerability on social media or forums before it's fixed

### Please DO:
- Email us directly at: security@kubently.io
- Use GitHub's private vulnerability reporting (if available)
- Allow us reasonable time to respond and fix the issue before public disclosure

## What to include in your report:

Please include the following details:

- Type of issue (e.g., buffer overflow, SQL injection, cross-site scripting, etc.)
- Full paths of source file(s) related to the manifestation of the issue
- The location of the affected source code (tag/branch/commit or direct URL)
- Any special configuration required to reproduce the issue
- Step-by-step instructions to reproduce the issue
- Proof-of-concept or exploit code (if possible)
- Impact of the issue, including how an attacker might exploit it

## Response Timeline

- **Initial Response**: Within 48 hours, we will acknowledge receipt of your report
- **Assessment**: Within 7 days, we will provide an initial assessment of the vulnerability
- **Resolution**: We aim to release a patch within 30 days of verification
- **Disclosure**: We will coordinate disclosure timing with you

## Security Update Process

1. The security team will investigate the issue and determine its severity
2. A fix will be developed and tested
3. A security advisory will be prepared
4. The fix will be released along with the security advisory
5. Credit will be given to the reporter (unless they wish to remain anonymous)

## Bug Bounty Program

Currently, we do not offer a paid bug bounty program. However, we deeply appreciate responsible disclosure and will:

- Acknowledge your contribution in our security advisories
- Add your name to our security hall of fame (with your permission)
- Provide a letter of appreciation upon request

## Security Best Practices for Users

### API Keys and Secrets
- Never commit API keys or secrets to the repository
- Use environment variables or Kubernetes secrets for sensitive data
- Rotate API keys regularly
- Use least-privilege principles for service accounts

### Kubernetes Security
- Keep your Kubernetes cluster updated
- Use RBAC (Role-Based Access Control) appropriately
- Enable audit logging
- Use network policies to restrict pod-to-pod communication
- Regularly scan container images for vulnerabilities

### Deployment Security
- Always use HTTPS/TLS for API endpoints
- Enable authentication and authorization
- Use resource limits and quotas
- Implement proper logging and monitoring
- Regular security audits

## Known Security Considerations

### LLM API Keys
Kubently requires API keys for LLM providers (Gemini, OpenAI, Anthropic, etc.). These should be:
- Stored securely in Kubernetes secrets or environment variables
- Never logged or exposed in error messages
- Rotated regularly
- Scoped with minimal required permissions

### Cluster Access
Kubently requires kubectl access to debug clusters. Ensure:
- Service accounts have minimal required permissions
- RBAC policies are properly configured
- Audit logging is enabled for kubectl operations
- Access is time-limited when possible

## Contact

For any security concerns that shouldn't be publicly disclosed, please contact:
- Email: security@kubently.io
- GPG Key: [Coming Soon]

For general questions about security, you can open a public issue with the `security` label.

## Acknowledgments

We would like to thank the following individuals for responsibly disclosing security issues:

- _Your name could be here!_

---

This security policy is adapted from best practices recommended by the [OpenSSF](https://openssf.org/) and GitHub's security guidelines.