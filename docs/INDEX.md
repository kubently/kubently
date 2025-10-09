# Kubently Documentation Index

## 📚 Documentation Structure

### Core Documentation

#### System Overview
- **[README.md](../README.md)** - Project overview, quick start, and feature highlights
- **[SYSTEM_DESIGN.md](SYSTEM_DESIGN.md)** - Complete system architecture and design philosophy
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - Detailed technical architecture and component design

#### Operational Guides
- **[DEPLOYMENT.md](DEPLOYMENT.md)** - Production deployment, configuration, and operations
- **[API.md](API.md)** - Complete API reference with endpoints and examples
- **[DEVELOPMENT.md](DEVELOPMENT.md)** - Developer guide, testing, and contribution guidelines

### Module Specifications

Located in `docs/modules/`:

1. **[01-api-core.md](modules/01-api-core.md)** - API Core module specification
2. **[02-auth.md](modules/02-auth.md)** - Authentication module specification
3. **[03-session.md](modules/03-session.md)** - Session management module specification
4. **[04-queue.md](modules/04-queue.md)** - Queue system module specification
5. **[05-agent.md](modules/05-agent.md)** - Agent module specification
6. **[06-models.md](modules/06-models.md)** - Data models and primitives specification
7. **[07-deployment.md](modules/07-deployment.md)** - Deployment automation specification

### Component Documentation

- **[kubently/agent/README.md](../kubently/agent/README.md)** - Agent deployment and operation guide
- **[deployment/README.md](../deployment/README.md)** - Deployment directory structure and usage

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

*Last Updated: 2024*
*Documentation Version: 1.0.0*
*For Kubently Version: 1.0.0*
