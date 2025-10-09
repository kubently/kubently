# Kubernetes Test Scenarios

This directory contains 20 intentionally broken Kubernetes configurations designed to test diagnostic capabilities of troubleshooting tools.

## Quick Start

```bash
# Make scripts executable
chmod +x *.sh

# List all scenarios
./run-all-scenarios.sh

# Run a specific scenario (e.g., scenario 3)
./run-all-scenarios.sh 3

# Clean up a specific scenario
./run-all-scenarios.sh cleanup 3

# Clean up all scenarios
./run-all-scenarios.sh cleanup
```

## Scenarios Overview

### Part 1: Pod & Container Failures
1. **ImagePullBackOff (Typo)** - Invalid image name
2. **ImagePullBackOff (Private Registry)** - Missing imagePullSecrets
3. **CrashLoopBackOff** - Container exits with error
4. **RunContainerError (Missing ConfigMap)** - Referenced ConfigMap doesn't exist
5. **RunContainerError (Missing Secret Key)** - Secret key doesn't exist
6. **OOMKilled** - Memory limit too low
7. **Failed Readiness Probe** - Probe path returns 404
8. **Failing Liveness Probe** - Causes continuous restarts

### Part 2: Deployment & Scheduling Failures
9. **Mismatched Labels** - Selector doesn't match pod template
10. **Unschedulable (Resources)** - Requests impossible resources
11. **Unschedulable (Taint)** - No matching toleration
12. **PVC Unbound** - StorageClass doesn't exist

### Part 3: Service & Network Failures
13. **Service Selector Mismatch** - Service has 0 endpoints
14. **Service Port Mismatch** - TargetPort doesn't match container
15. **Default Deny Ingress** - NetworkPolicy blocks traffic
16. **Default Deny Egress** - Can't reach external IPs
17. **Cross-Namespace Block** - NetworkPolicy blocks cross-namespace

### Part 4: Config & RBAC Failures
18. **Missing ServiceAccount** - Pod references non-existent SA
19. **RBAC Forbidden (Role)** - Insufficient permissions
20. **RBAC Forbidden (ClusterRole)** - Wrong binding type for cross-namespace

## Individual Script Usage

Each scenario can also be run directly:
```bash
./01-imagepullbackoff-typo.sh
```

## Cleanup

Each scenario creates its own namespace (test-scenario-XX). To clean up:
```bash
# Individual namespace
kubectl delete namespace test-scenario-01

# All test namespaces
for i in {01..20}; do kubectl delete namespace test-scenario-$i; done
```

Note: Scenario 11 adds a node taint that should be removed:
```bash
kubectl taint nodes <node-name> app=critical:NoSchedule-
```