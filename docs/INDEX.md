# Kubently Documentation Index

## 📚 Documentation Structure

### Getting Started
- **[README.md](../README.md)** - Project overview, quick start, and feature highlights
- **[QUICK_START.md](QUICK_START.md)** - Single-cluster install in about five minutes
- **[GETTING_STARTED.md](GETTING_STARTED.md)** - Production setup: secrets, ingress, remote executors
- **[DEPLOYMENT.md](DEPLOYMENT.md)** - Deployment options, configuration management, hardening, upgrades
- **[ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md)** - Every variable the API and executor read

### Architecture
- **[SYSTEM_DESIGN.md](SYSTEM_DESIGN.md)** - System architecture and design philosophy
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - Technical architecture and component design
- **[SSE_ARCHITECTURE.md](SSE_ARCHITECTURE.md)** - SSE + POST + Redis pub/sub command channel

### Protocols & Integrations
- **[API.md](API.md)** - REST API reference with endpoints and examples
- **[TEST_QUERIES.md](TEST_QUERIES.md)** - Exact A2A protocol request examples
- **[A2A_CONFIGURATION.md](A2A_CONFIGURATION.md)** - A2A server configuration and agent card
- **[MCP.md](MCP.md)** - Kubently **as** an MCP server (Claude Desktop, Cursor, custom agents)
- **[MCP_CLIENT_TOOLS.md](MCP_CLIENT_TOOLS.md)** - Kubently **as** an MCP client: mounting external MCP servers, including per-request injection
- **[AGENTGATEWAY_SETUP.md](AGENTGATEWAY_SETUP.md)** - Running agentgateway in front of Kubently

### Agent Capabilities
- **[PROMPTS.md](PROMPTS.md)** - How the A2A system prompt is externalized and overridden
- **[CLOUD_TELEMETRY.md](CLOUD_TELEMETRY.md)** - Read-only CloudWatch / Cloud Logging access via workload identity (default off)
- **[CLOUD_AUTH.md](CLOUD_AUTH.md)** - How the executor pod obtains a cloud identity for `kubectl`
- **[GITOPS_REMEDIATION.md](GITOPS_REMEDIATION.md)** - Agent-proposed fix PRs, human-merged (default off)

### Security & Auth
- **[A2A_AUTHENTICATION.md](A2A_AUTHENTICATION.md)** - Authenticating A2A callers
- **[AUTH_DISCOVERY.md](AUTH_DISCOVERY.md)** - Auth discovery endpoint and OIDC configuration
- **[OAUTH_USAGE.md](OAUTH_USAGE.md)** - OAuth/OIDC login flow for the CLI
- **[TLS_DEPLOYMENT.md](TLS_DEPLOYMENT.md)** - TLS termination patterns
- **[MULTI_CLUSTER_TLS.md](MULTI_CLUSTER_TLS.md)** - TLS between remote executors and the central API

### Operations & Development
- **[DEVELOPMENT.md](DEVELOPMENT.md)** - Developer guide, testing, and contribution guidelines
- **[DOCKER_BUILD_GUIDE.md](DOCKER_BUILD_GUIDE.md)** - Building and publishing images to ghcr.io
- **[LANGSMITH_TRACING.md](LANGSMITH_TRACING.md)** - Production tracing and observability

### Module Specifications

Located in `docs/modules/`:

1. **[01-api-core.md](modules/01-api-core.md)** - API Core module specification
2. **[02-auth.md](modules/02-auth.md)** - Authentication module specification
3. **[03-session.md](modules/03-session.md)** - Session management module specification
4. **[04-queue.md](modules/04-queue.md)** - Queue system module specification
5. **[05-agent.md](modules/05-agent.md)** - Agent module specification
6. **[06-models.md](modules/06-models.md)** - Data models and primitives specification
7. **[07-deployment.md](modules/07-deployment.md)** - Deployment automation specification
8. **[08-cli.md](modules/08-cli.md)** - CLI specification

### Component Documentation

- **[deployment/README.md](../deployment/README.md)** - Deployment directory structure and usage
- **[deployment/helm/kubently/values.yaml](../deployment/helm/kubently/values.yaml)** - The chart's annotated defaults; the source of truth for every Helm setting
- **[CLAUDE.md](../CLAUDE.md)** - Development guidelines and repository conventions

### Planning Notes

`docs/plans/` and **[RESUME_PUNCHLIST.md](RESUME_PUNCHLIST.md)** hold
forward-looking planning material. They describe intent, not shipped behaviour
— check the code or the guides above before relying on anything in them.

## 📖 Reading Order

### For Users/Operators
1. Start with [README.md](../README.md)
2. Review [DEPLOYMENT.md](DEPLOYMENT.md) for installation
3. Reference [API.md](API.md) for integration

### For Developers
1. Read [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md) for philosophy
2. Study [ARCHITECTURE.md](ARCHITECTURE.md) for technical details
3. Review relevant module specs in `modules/`
4. Follow [DEVELOPMENT.md](DEVELOPMENT.md) for contribution

### For Module Implementers
1. Read [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md) for design principles
2. Study [modules/06-models.md](modules/06-models.md) for data structures
3. Review your assigned module specification
4. Reference other modules only via their public interfaces

## 🏗️ Documentation Principles

### Consistency
- All docs use Python 3.13 as the standard version
- All examples use consistent naming conventions
- All code blocks include language specifiers

### Modularity
- Each module has its own specification
- Module docs define interfaces, not implementations
- Cross-references use relative paths

### Maintenance
- Single source of truth for each topic
- No duplicate information across documents
- Regular review and updates with releases

## 🔄 Documentation Updates

When updating documentation:

1. **API Changes**: Update [API.md](API.md) and relevant module specs
2. **New Features**: Update README.md and create/update relevant docs
3. **Deployment Changes**: Update [DEPLOYMENT.md](DEPLOYMENT.md)
4. **Architecture Changes**: Update [ARCHITECTURE.md](ARCHITECTURE.md) and [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md)

## 📝 Documentation Standards

### Markdown Guidelines
- Use ATX-style headers (`#` not underlines)
- Include TOC for documents > 3 sections
- Use fenced code blocks with language identifiers
- Keep line length < 120 characters for readability

### Code Examples
- Provide working examples where possible
- Include error handling in examples
- Use type hints in Python code
- Add comments for complex logic

### Versioning
- Document version compatibility
- Note breaking changes clearly
- Include migration guides when needed
- Tag docs with release versions

---

*Chart version: see `deployment/helm/kubently/Chart.yaml`*
