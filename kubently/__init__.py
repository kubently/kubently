"""
Kubently - Kubernetes Debugging System

A system for debugging Kubernetes clusters through AI.

Architecture:
- Each module is self-contained with clear interfaces
- Modules are completely replaceable
- No module knows the internals of another
- All communication through defined interfaces

Modules:
- auth: Authentication and authorization
- session: Debugging session management
- queue: Command queuing and distribution
- storage: Data persistence abstraction
- api: REST API interface
- a2a: Agent-to-agent protocol
- agent: Kubernetes cluster agent
- executor: Command execution logic
"""

__version__ = "1.0.0"
