"""
Agent Module - Black Box Interface

Purpose: Kubernetes cluster agent that executes kubectl commands
Interface: HTTP long-polling for commands, execute and return results
Hidden: kubectl execution details, command validation, cluster connection

Can be replaced with different execution mechanisms (direct K8s API, different languages).
"""

# Agent runs as separate deployment, communicates via API
