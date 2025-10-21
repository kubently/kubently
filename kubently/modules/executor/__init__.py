"""
Executor Module - Black Box Interface

Purpose: Kubernetes cluster agent that executes kubectl commands
Interface: Server-Sent Events (SSE) for real-time command streaming, HTTP POST for results
Hidden: kubectl execution details, command validation, cluster connection

Can be replaced with different execution mechanisms (direct K8s API, different languages).
"""

# Executor runs as separate deployment, uses SSE for instant command delivery
