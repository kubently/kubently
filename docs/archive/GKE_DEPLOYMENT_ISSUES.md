# GKE Production Deployment - Issues & Fixes

**Date**: 2025-10-04
**Deployment**: GKE test-api.kubently.io
**Status**: ✅ Resolved

## Summary

This document tracks issues discovered during the GKE production deployment and their resolutions. These issues need to be addressed in the Helm chart and deployment documentation.

---

## Issue 1: Executor Token Registration

**Problem**: Executor tokens from Kubernetes secrets are not automatically registered in Redis. The API validates executor tokens by checking Redis key `executor:token:{cluster_id}`, but tokens stored in K8s secrets aren't loaded into Redis at startup.

**Symptom**: Executors get 401 Unauthorized when connecting to `/executor/stream` endpoint.

**Root Cause**:
- Tokens exist in K8s secret `kubently-api-tokens` with keys: `api-key`, `gke-executor`, `kind-remote`
- But `verify_executor_auth()` in `main.py:212` checks Redis: `await redis_client.get(f"executor:token:{x_cluster_id}")`
- No code loads K8s secret tokens into Redis at startup

**Manual Fix Applied**:
```bash
# Register executor token in Redis
kubectl exec -n kubently kubently-redis-master-0 -- \
  redis-cli SET "executor:token:kind-kubently" "c2ca51fcefce690817d3f0649c143d347d73505b02fcb9d5c17cde29fb14ad12"
```

**Permanent Fix Needed**:
1. Add startup script to API deployment that reads tokens from K8s secret and loads them into Redis
2. OR: Update `verify_executor_auth()` to check both Redis AND environment variables from secrets
3. OR: Create init container that populates Redis from K8s secrets

**Helm Chart Changes Required**:
- Add init container or startup hook to sync executor tokens to Redis
- Environment variables needed: `EXECUTOR_TOKENS` with format `cluster_id1:token1,cluster_id2:token2`

**Files to Update**:
- `deployment/helm/kubently/templates/api-deployment.yaml` - Add init container
- `kubently/main.py` - Add token sync logic or fallback to env vars
- `docs/PRODUCTION_DEPLOYMENT_PLAN_REVIEWED.md` - Document token registration step

---

## Issue 2: API Key Format in Secrets

**Problem**: API key secret format is backwards from what the code expects.

**Root Cause**:
- Code in `kubently/modules/auth/auth.py:54-56` parses format as `service:key`
- Deployment plan documented it as `key:service`
- This causes API key validation to fail with "Invalid API key"

**Code Snippet**:
```python
# auth.py line 54-56
if ":" in entry:
    service, key = entry.split(":", 1)
    keys[key] = service  # Stores keys[key] = service
```

**Wrong**:
```bash
kubectl create secret generic kubently-api-keys \
  --from-literal=keys="da4f779e...ed6aa3:cli-user"  # key:service - WRONG
```

**Correct**:
```bash
kubectl create secret generic kubently-api-keys \
  --from-literal=keys="cli-user:da4f779e...ed6aa3"  # service:key - CORRECT
```

**Manual Fix Applied**:
```bash
kubectl delete secret kubently-api-keys -n kubently
kubectl create secret generic kubently-api-keys -n kubently \
  --from-literal=keys="cli-user:da4f779e...ed6aa3"
kubectl rollout restart deployment kubently-api -n kubently
```

**Permanent Fix Needed**:
- Update documentation to clarify format is `service:key`
- Add validation/error message to code that explains correct format

**Files to Update**:
- `docs/PRODUCTION_DEPLOYMENT_PLAN_REVIEWED.md` - Fix API key format examples
- `kubently/modules/auth/auth.py` - Add clearer error message about format
- `deployment/helm/kubently/values.yaml` - Add comments explaining format

---

## Issue 3: LLM Secret Name Mismatch

**Problem**: Helm deployment expects different secret name than production plan specifies.

**Symptom**: API crashes with `OSError: ANTHROPIC_API_KEY environment variable is required`

**Root Cause**:
- Deployment template references secret `llm-api-keys` with key `anthropic-key`
- Production plan creates secret `kubently-llm-secrets` with key `ANTHROPIC_API_KEY`
- Mismatch causes env var to not be populated

**Deployment YAML** (current):
```yaml
- name: ANTHROPIC_API_KEY
  valueFrom:
    secretKeyRef:
      key: anthropic-key
      name: llm-api-keys    # Looking for this secret
```

**Production Plan** (creates this):
```bash
kubectl create secret generic kubently-llm-secrets \
  --from-literal=ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}"
```

**Manual Fix Applied**:
```bash
# Created the secret the deployment expects
kubectl create secret generic llm-api-keys -n kubently \
  --from-literal=anthropic-key="sk-ant-api03-..."
kubectl rollout restart deployment kubently-api -n kubently
```

**Permanent Fix Needed**:
Either:
1. Update Helm template to use `kubently-llm-secrets` with key `ANTHROPIC_API_KEY`
2. OR: Update production plan to create `llm-api-keys` with key `anthropic-key`

**Recommendation**: Use option 1 (update Helm template) for consistency with naming convention.

**Files to Update**:
- `deployment/helm/kubently/templates/api-deployment.yaml` - Update secret reference
- `deployment/helm/kubently/values.yaml` - Document correct secret name
- `docs/PRODUCTION_DEPLOYMENT_PLAN_REVIEWED.md` - Match Helm template expectations

---

## Issue 4: CLI Double /a2a Path

**Problem**: CLI builds URL with double `/a2a/` path causing 404 errors.

**Symptom**:
- Requests go to `/a2a/a2a/` instead of `/a2a/`
- Logs show: `"POST /a2a/a2a/ HTTP/1.1" 404`

**Root Cause**:
- `debug.ts:210` appends `/a2a/` to apiUrl before passing to `runDebugSession()`
- `a2aClient.ts:64` ALSO appends `/a2a/` to the baseURL
- Result: double path

**Code Flow**:
```typescript
// debug.ts line 210
const debugApiUrl = apiUrl.replace(/\/$/, '') + a2aPath + '/';  // Adds /a2a/
await runDebugSession(debugApiUrl, apiKey, clusterId);

// a2aClient.ts line 64 (BEFORE FIX)
baseURL: apiUrl.replace(/\/$/, '') + '/a2a/',  // Adds /a2a/ AGAIN
```

**Fix Applied**:
```typescript
// a2aClient.ts line 64 (AFTER FIX)
baseURL: apiUrl, // Keep trailing slash as-is, don't add /a2a/
```

**Files Updated**:
- `kubently-cli/nodejs/src/lib/a2aClient.ts` - Line 64

**Additional Issues Found**:
- Without trailing slash: `POST /a2a HTTP/1.1" 307` (redirect to /a2a/)
- With trailing slash: `POST /a2a/ HTTP/1.1" 200` (success)
- FastAPI requires trailing slash on mount paths

---

## Issue 5: Redis Password Not Configured (Security Risk)

**Problem**: Production deployment plan enables Redis authentication but doesn't set password.

**From Plan** (`docs/PRODUCTION_DEPLOYMENT_PLAN_REVIEWED.md:403`):
```yaml
redis:
  auth:
    enabled: true  # CRITICAL: Enable authentication
    password: ""   # Auto-generated if empty
```

**Current Status**:
- Redis is accessible without password (verified in kubently namespace)
- Security risk identified in AI review not actually fixed

**Fix Required**:
- Generate secure Redis password
- Update Helm values to set password
- Ensure API deployment has Redis password in env vars

**Files to Update**:
- `deployment/helm/gke-production-values.yaml` - Add Redis password
- `docs/PRODUCTION_DEPLOYMENT_PLAN_REVIEWED.md` - Update Redis security section

---

## Testing Checklist

After applying fixes, verify:

- [ ] Executor connects successfully (no 401 errors)
  ```bash
  kubectl logs -n kubently -l app=kubently-executor --tail=20
  # Should see: "SSE connection established"
  ```

- [ ] API key authentication works
  ```bash
  curl -H "X-API-Key: {key}" https://test-api.kubently.io/a2a/
  # Should NOT return 401
  ```

- [ ] LLM initialization succeeds
  ```bash
  kubectl logs -n kubently -l app.kubernetes.io/name=kubently --tail=100 | grep ANTHROPIC
  # Should NOT see "ANTHROPIC_API_KEY environment variable is required"
  ```

- [ ] CLI can query clusters
  ```bash
  kubently debug --api-url https://test-api.kubently.io --api-key {key}
  # Ask: "what clusters do you have"
  # Should get response listing clusters
  ```

- [ ] Executor token in Redis
  ```bash
  kubectl exec -n kubently kubently-redis-master-0 -- redis-cli GET "executor:token:kind-kubently"
  # Should return token value
  ```

---

## Deployment Script Updates Needed

Create `deployment/scripts/sync-secrets-to-redis.sh`:
```bash
#!/bin/bash
# Sync K8s secrets to Redis for executor tokens

NAMESPACE=${NAMESPACE:-kubently}
REDIS_POD=$(kubectl get pod -n $NAMESPACE -l app=redis -o jsonpath='{.items[0].metadata.name}')

# Read executor tokens from secret
GKE_TOKEN=$(kubectl get secret kubently-api-tokens -n $NAMESPACE -o jsonpath='{.data.gke-executor}' | base64 -d)
KIND_TOKEN=$(kubectl get secret kubently-api-tokens -n $NAMESPACE -o jsonpath='{.data.kind-remote}' | base64 -d)

# Write to Redis
kubectl exec -n $NAMESPACE $REDIS_POD -- redis-cli SET "executor:token:gke" "$GKE_TOKEN"
kubectl exec -n $NAMESPACE $REDIS_POD -- redis-cli SET "executor:token:kind-kubently" "$KIND_TOKEN"

echo "✓ Executor tokens synced to Redis"
```

Add to deployment plan after Helm install step.

---

## Summary of Required Actions

### Immediate (For Next Deployment)
1. ✅ Fix API key format in documentation
2. ✅ Fix LLM secret name in Helm template
3. ✅ Fix CLI double /a2a path
4. ⚠️ Create executor token sync script
5. ⚠️ Add Redis password to values

### Medium Term (Before Production GA)
1. Add init container to Helm chart for automatic token sync
2. Add validation for API key format with helpful error messages
3. Add health check to verify executor registration
4. Document all secret formats clearly in Helm values comments

### Long Term (Tech Debt)
1. Consolidate secret naming conventions
2. Add automated tests for secret configuration
3. Create validation script that checks all secrets before deployment
4. Add Redis authentication with password rotation

---

## Files Modified in This Session

1. ✅ `kubently-cli/nodejs/src/lib/a2aClient.ts` - Fixed double /a2a path
2. 📝 `docs/GKE_DEPLOYMENT_ISSUES.md` - This file (NEW)

## Files That Need Updates

1. 🔧 `deployment/helm/kubently/templates/api-deployment.yaml` - LLM secret name
2. 🔧 `deployment/helm/kubently/values.yaml` - Secret format documentation
3. 🔧 `docs/PRODUCTION_DEPLOYMENT_PLAN_REVIEWED.md` - API key format, LLM secrets
4. 🔧 `deployment/scripts/sync-secrets-to-redis.sh` - NEW FILE NEEDED
5. 🔧 `deployment/helm/gke-production-values.yaml` - Redis password

---

## Contact

For questions about these fixes:
- See deployment plan: `docs/PRODUCTION_DEPLOYMENT_PLAN_REVIEWED.md`
- Check helm chart: `deployment/helm/kubently/`
- Review API auth: `kubently/modules/auth/auth.py`
