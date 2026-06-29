#!/usr/bin/env bash
# Local E2E baseline: kind cluster `kubently` (ctx kind-kubently, used by test-automation
# scenarios) running kubently (api+redis+a2a) plus an in-cluster executor registered as
# logical cluster_id `kind` (what the test queries say: "In cluster kind ...").
#
# ponytail: one script, Helm-driven, idempotent. The only non-Helm glue is seeding the
# executor token into Redis (no template does this) so the API accepts the executor.
#
#   ANTHROPIC_API_KEY=sk-... ./deployment/scripts/kind-e2e.sh          # deploy
#   ./deployment/scripts/kind-e2e.sh down                              # delete cluster
#   BUILD=false ./deployment/scripts/kind-e2e.sh                       # skip image rebuild
set -euo pipefail
cd "$(dirname "$0")/../.."

CLUSTER=kubently                 # -> kubectl context kind-kubently (scenarios depend on this)
NS=kubently
CLUSTER_ID=kind                  # logical id the agent/test queries reference
EXEC_TOKEN="${EXEC_TOKEN:-local-e2e-executor-token-0123456789abcdef}"   # 32-128 chars
CHART=deployment/helm/kubently
VALUES=deployment/helm/test-values.yaml
BUILD="${BUILD:-true}"

red()  { printf '\033[0;31m%s\033[0m\n' "$*"; }
grn()  { printf '\033[0;32m%s\033[0m\n' "$*"; }
step() { printf '\033[0;34m► %s\033[0m\n' "$*"; }

if [ "${1:-}" = "down" ]; then
  step "Deleting kind cluster $CLUSTER"
  kind delete cluster --name "$CLUSTER"
  exit 0
fi

command -v kind   >/dev/null || { red "kind not installed";   exit 1; }
command -v helm   >/dev/null || { red "helm not installed";   exit 1; }
command -v docker >/dev/null || { red "docker not installed"; exit 1; }
: "${ANTHROPIC_API_KEY:?Set ANTHROPIC_API_KEY (agent LLM provider is anthropic-claude)}"

# 1. Cluster --------------------------------------------------------------
if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  step "Creating kind cluster: $CLUSTER"
  kind create cluster --name "$CLUSTER"
else
  step "Reusing kind cluster: $CLUSTER"
fi
kubectl config use-context "kind-$CLUSTER" >/dev/null

# 2. Images (build from repo HEAD so baseline == current code) ------------
if [ "$BUILD" = "true" ]; then
  step "Building images"
  docker build -q -f deployment/docker/api/Dockerfile      -t kubently-api:latest .
  docker build -q -f deployment/docker/executor/Dockerfile -t kubently-executor:latest .
  step "Loading images into kind"
  kind load docker-image kubently-api:latest kubently-executor:latest --name "$CLUSTER"
fi

# 3. Namespace + secrets --------------------------------------------------
step "Creating namespace + secrets"
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NS" create secret generic kubently-redis-password \
  --from-literal=password=localdev --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NS" create secret generic kubently-api-keys \
  --from-literal=keys=test-api-key --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NS" create secret generic kubently-llm-secrets \
  --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  ${GOOGLE_API_KEY:+--from-literal=GOOGLE_API_KEY="$GOOGLE_API_KEY"} \
  --dry-run=client -o yaml | kubectl apply -f -

# 4. Helm -----------------------------------------------------------------
step "helm upgrade --install kubently"
HELM_ARGS=(
  --set api.enabled=true
  --set api.image.repository=kubently-api      --set api.image.tag=latest      --set api.image.pullPolicy=Never
  --set executor.image.repository=kubently-executor --set executor.image.tag=latest --set executor.image.pullPolicy=Never
  --set api.existingSecret=kubently-api-keys
  --set api.env.ANTHROPIC_MODEL_NAME="${ANTHROPIC_MODEL_NAME:-claude-sonnet-4-5}"
  --set executor.enabled=true
  --set executor.clusterId="$CLUSTER_ID"
  --set executor.apiUrl="http://kubently-api:8080"
  --set executor.token="$EXEC_TOKEN"
  --wait --timeout 180s
)
if ! helm upgrade --install kubently "$CHART" -f "$VALUES" -n "$NS" "${HELM_ARGS[@]}"; then
  # Server-side-apply can deadlock on .spec.replicas: once kube-controller-manager
  # owns that field (e.g. after a scale or rollout), Helm refuses to overwrite it.
  # The chart has no HPA, so dropping the Deployment lets Helm recreate it as owner.
  # ponytail: delete-and-retry once; upgrade to a clean `helm uninstall` if it recurs.
  red "helm upgrade failed (likely .spec.replicas field-manager conflict) — recreating kubently-api and retrying once"
  kubectl -n "$NS" delete deploy kubently-api --ignore-not-found
  helm upgrade --install kubently "$CHART" -f "$VALUES" -n "$NS" "${HELM_ARGS[@]}"
fi

# 5. Seed executor token into Redis (the missing glue) --------------------
step "Seeding executor token into Redis (executor:token:$CLUSTER_ID)"
kubectl -n "$NS" exec statefulset/kubently-redis-master -- \
  redis-cli -a localdev set "executor:token:$CLUSTER_ID" "$EXEC_TOKEN" >/dev/null
# bounce executor so it re-auths immediately instead of waiting out its backoff
kubectl -n "$NS" rollout restart deploy/kubently-executor
kubectl -n "$NS" rollout status  deploy/kubently-executor --timeout=90s

# 6. Port-forward + smoke -------------------------------------------------
pkill -f "port-forward.*kubently-api 8080" 2>/dev/null || true
step "Port-forwarding 8080 -> kubently-api"
kubectl -n "$NS" port-forward svc/kubently-api 8080:8080 >/dev/null 2>&1 &
sleep 3

fail=0

step "Smoke: /healthz"
curl -sf http://localhost:8080/healthz >/dev/null && grn "healthz OK" || { red "healthz FAILED"; fail=1; }

step "Smoke: cluster '$CLUSTER_ID' registered?"
ok=0
for i in $(seq 1 20); do
  if curl -sf -H "X-API-Key: test-api-key" http://localhost:8080/debug/clusters | grep -q "$CLUSTER_ID"; then
    grn "executor registered as cluster '$CLUSTER_ID'"; ok=1; break
  fi
  sleep 3
done
[ "$ok" = 1 ] || { red "executor did NOT register — kubectl -n $NS logs deploy/kubently-executor"; fail=1; }

step "Smoke: agent LLM round-trip (real tool call through executor)"
resp=$(curl -s -m 90 -X POST http://localhost:8080/a2a/ \
  -H "Content-Type: application/json" -H "X-API-Key: test-api-key" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":\"1\",\"method\":\"message/stream\",\"params\":{\"message\":{\"messageId\":\"smoke\",\"role\":\"user\",\"parts\":[{\"partId\":\"p1\",\"text\":\"In cluster $CLUSTER_ID, list pods in kube-system and report what you see.\"}]}}}")
if printf '%s' "$resp" | grep -qiE "not_found_error|error code: 4|encountered an error"; then
  red "agent round-trip FAILED — likely bad ANTHROPIC_MODEL_NAME. Response:"; printf '%s\n' "$resp" | grep -o '"text":"[^"]*"' | head -3; fail=1
else
  grn "agent round-trip OK"
fi

cat <<EOF

$( [ "$fail" = 0 ] && grn "Baseline up." || red "Baseline came up with FAILURES (see above)." )  context=kind-$CLUSTER  ns=$NS  cluster_id=$CLUSTER_ID  api=http://localhost:8080
Run the E2E suite (capture baseline tool calls):
  cd test-automation && ./run_tests.sh test-only --api-key test-api-key --scenario 03-crashloopbackoff
Tear down:
  $0 down
EOF
exit $fail
