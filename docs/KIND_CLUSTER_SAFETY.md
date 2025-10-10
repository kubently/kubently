# Kind Cluster Safety Guide

## ⚠️ CRITICAL: Persistent Development Cluster

The Kubently project uses a **persistent Kind cluster** for development and testing. This cluster should **NEVER** be deleted without explicit user permission.

### Cluster Details

- **Name**: `kubently` (transitioning to `kind-kubently` for consistency)
- **Context**: `kind-kubently`
- **Purpose**: Persistent development environment
- **Contains**: 
  - kubently-api deployments
  - kubently-agent configurations
  - Redis data and state
  - Development sessions and data

## Safe Operations

### ✅ Safe Commands (No Data Loss)

```bash
# Create cluster (only if it doesn't exist)
make kind-create

# Deploy new code (updates existing deployments)
make kind-deploy

# View logs
make kind-logs
kubectl logs -n kubently -l app=kubently-api

# Port forwarding
kubectl port-forward -n kubently svc/kubently-api 8080:80
kubectl port-forward -n kubently svc/kubently-api 8000:8000

# Check status
kubectl get pods -n kubently
kubectl get all -n kubently

# Update deployments (rolling update)
kubectl rollout restart deployment/kubently-api -n kubently
```

### 🚨 DANGEROUS Operations (Data Loss Risk)

These operations are **BLOCKED** in the Makefile:

```bash
# ❌ BLOCKED: This would destroy the development cluster!
make kind-delete  # Will show error and refuse to run

# ❌ DANGEROUS: Manual deletion (ask user first!)
# Current cluster name: kubently (may transition to kind-kubently)
kind delete cluster --name kubently

# ❌ DANGEROUS: Namespace deletion
kubectl delete namespace kubently
```

## If Cluster Deletion is Required

If there's a genuine need to delete the cluster (corruption, major issues, etc.):

1. **Ask the user explicitly**: "Should I delete the kind-kubently cluster? This will destroy all development data."
2. **Wait for confirmation**: Do not proceed without explicit "yes"
3. **Document the reason**: Why deletion is necessary
4. **Manual execution**: Use the manual command: `kind delete cluster --name kind-kubently`

## Recovery Procedures

If the cluster is accidentally deleted:

```bash
# 1. Recreate the cluster
make kind-create

# 2. Redeploy everything
make kind-deploy

# 3. Note: All previous agent configurations and session data will be lost
```

## Makefile Safeguards

The `make kind-delete` target is configured to:
1. Display an error message
2. Explain the risks
3. Refuse to execute (`@false`)
4. Show the manual command if truly needed

## Development Workflow

### Normal Development Cycle

1. Write code changes
2. `make kind-deploy` (updates existing cluster)
3. Test with port-forwarding
4. Repeat

### If Cluster Issues Occur

1. Check cluster status: `kubectl get nodes`
2. Check pod status: `kubectl get pods -n kubently`
3. View logs: `make kind-logs`
4. Try deployment restart: `kubectl rollout restart deployment/kubently-api -n kubently`
5. **Only if all else fails**: Ask user for permission to recreate cluster

## Port Mappings

The Kind cluster maps these ports to localhost:

- `8080` → kubently-api REST endpoint (container port 30080)
- `8000` → kubently-api A2A endpoint (container port 30088)  
- `6379` → Redis (container port 30379)

These are configured in `deployment/kubernetes/kind-config.yaml`.

## Remember

- The cluster name is `kind-kubently` (not `kubently`)
- It contains valuable development state
- Deletion should be the absolute last resort
- Always ask the user before destructive operations
