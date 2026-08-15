# Scheduled Fleet Health Digest Design

**Date:** 2026-08-15
**Status:** Draft
**Issue:** #54
**Depends on:** #53 (fleet fan-out, shipped)

## Problem

The proactive path is reactive only: Alertmanager fires → `/webhooks/alertmanager`
→ agent diagnosis → Slack. Nothing tells an operator about a fleet that is quietly
degrading — a cluster where pods have been `CrashLoopBackOff` for a day but no
alert rule covers it, or a cluster that stopped reporting entirely.

Fleet fan-out (#53) made "what is broken across every cluster" a single agent call.
Nothing calls it on a schedule.

## Approach

Three pieces, each reusing something that already exists.

### 1. Trigger: Helm CronJob → HTTP endpoint

`CronJob` in the chart POSTs to a new endpoint on the API Service. Kubernetes is
already the scheduler; an in-process scheduler would mean a new dependency
(APScheduler et al.) **and** leader election, because the API runs multiple
replicas behind a Service and every replica would otherwise fire its own digest.

The endpoint is independently useful: `curl -X POST .../webhooks/fleet-report`
runs the digest on demand, which is also how the E2E test drives it.

CronJob uses the API image (already pulled on the node, ships `httpx`) rather
than adding a `curl` image to the deployment's pull set.

### 2. Endpoint: `POST /webhooks/fleet-report`

Lives in `kubently/modules/webhook/` (new `fleet_report.py`), registered by the
same router factory pattern as `alertmanager.py`:

- `Depends(verify_api_key)` — same API-key auth, no new auth path
- Optional JSON body, both fields absent by default:
  `{"query": "<override>", "dry_run": true}`
- 503 if `SLACK_WEBHOOK_URL` is unset — **except** under `dry_run`, which never
  posts and so has no such precondition
- **Scheduled mode** (no `dry_run`): ACKs **202 immediately**, diagnosis runs in
  `asyncio.create_task`. A fleet sweep takes minutes and a CronJob holding an
  HTTP connection that long is a timeout waiting to happen. The CronJob's success
  means "digest started"; failures surface in API logs and in the absence of a
  Slack post
- **Dry-run mode**: runs **synchronously** and returns `200` with
  `{"query": ..., "answer": ...}` — the caller is a human who wants to read the
  digest, and waiting is the entire point. Nothing is posted to Slack
- The heavy `KubentlyAgent` import stays inside the background task (and inside
  the dry-run handler), preserving the property that the API boots without the
  a2a stack

### 3. The scan: one `ask_kubently` call, not a scan engine

The digest asks the agent one fleet-health question and posts the answer. No
per-cluster orchestration code: the agent already has `execute_kubectl_multi` and
the "Fleet Queries" prompt section that steers it there, and fan-out already
collapses empty per-cluster results to one line — so a healthy fleet is cheap in
tokens before the model ever sees it.

#### The query is user-owned, not hardcoded

Digest wording is exactly the thing every team wants different — one shop cares
about PVC pressure, another about cert expiry, another wants it in their own
language. The query is therefore a **prompt file**, loaded through the config
module's existing `get_prompt()` (`kubently/modules/config/prompts.py`), not a
string constant:

```python
get_prompt(role="fleet_report", default_filename="fleet_report.prompt.yaml")
```

That mechanism already gives us, for free, everything customization needs:

| Layer | How | For |
|---|---|---|
| Ship a sane default | `prompts/fleet_report.prompt.yaml`, mounted via the chart's existing prompt ConfigMap pattern | everyone |
| Edit the wording | `fleetReport.query` in `values.yaml` → rendered into that ConfigMap's `content` | most users |
| Full control | `KUBENTLY_FLEET_REPORT_PROMPT_FILE` → any mounted file, with the spec's variable substitution | power users |
| Try a wording *right now* | `query` in the POST body | iterating |

The last row is the one that makes the other three usable: you iterate with
`dry_run` + `query` against a live fleet until the digest reads right, then paste
the winner into `values.yaml`. No `helm upgrade` per attempt.

Note the spec format's `role` field must literally be `system` (validator in
`prompts.py:25`); the `role="fleet_report"` argument only selects the env-var
name. Worth a comment in the shipped file so nobody "fixes" it.

Default content, roughly:

```
Check the health of every registered cluster. For each one, report pods that are
not Running or Succeeded and any recent Warning events.
Format the answer for Slack: one short section per cluster; a cluster with
nothing wrong gets a single line saying it is healthy.
If the whole fleet is healthy, say so in one line and stop.
```

**Trade-off, stated plainly:** one agent call for the whole fleet produces a
shallower digest than N per-cluster investigations would. It is also ~1/N the LLM
cost and finishes in one agent run instead of N. Start here; if digests read as
too shallow, the upgrade path is a second pass that re-asks per cluster only for
clusters the first pass flagged. That is a prompt-and-loop change, not a
re-architecture.

**Known ceiling:** fan-out is capped at 10 clusters per call
(`KUBENTLY_MAX_FLEET_CLUSTERS`). Above the cap the agent is told to batch, and
whether it reliably does so across a 30-cluster fleet is exactly what the first
real deployment will tell us. Documented, not pre-solved.

### Slack post

Reuses the alert path's shape — one `httpx.POST` to `SLACK_WEBHOOK_URL`:

```
:satellite: *Kubently fleet health digest*

<agent answer>
```

One message, not one per cluster: a daily digest that pages five separate
messages into a channel is how the feature gets muted.

### 4. Running it once

Nobody should have to wait until 13:00 Monday to find out whether their digest
works, and nobody should have to point a test run at a real Slack channel. Three
ways in, no new code beyond the `dry_run` flag:

**Preview it, post nothing** — the loop you actually use while tuning wording:

```bash
curl -X POST http://localhost:8080/webhooks/fleet-report \
  -H "X-API-Key: $KUBENTLY_API_KEY" -H 'Content-Type: application/json' \
  -d '{"dry_run": true, "query": "List every cluster with a pod restarting more than 5 times."}'
```

Returns the rendered digest as JSON. No Slack, no schedule, no redeploy.

**Post it for real, once** — proves the Slack wiring end to end:

```bash
curl -X POST http://localhost:8080/webhooks/fleet-report -H "X-API-Key: $KUBENTLY_API_KEY"
```

**Fire the actual CronJob once** — proves the *scheduled* path (image, service
account, in-cluster URL, API key mount), which the two curls above bypass:

```bash
kubectl create job --from=cronjob/kubently-fleet-report fleet-report-test -n kubently
```

That last one is why the trigger is a CronJob rather than an in-process
scheduler: `--from=cronjob` is a free, native one-shot of the real thing. An
in-process scheduler would have needed us to build a manual-trigger path
ourselves.

Pairs with `fleetReport.suspend` below: install with `enabled: true,
suspend: true` to get the CronJob object without a schedule, trigger it by hand
until happy, then unsuspend.

## Helm

```yaml
fleetReport:
  enabled: false            # opt-in
  suspend: false            # create the CronJob but don't run it (manual triggering only)
  schedule: "0 13 * * 1-5"  # weekday mornings; UTC unless the cluster says otherwise
  query: ""                 # override the shipped digest prompt; empty = use prompts/fleet_report.prompt.yaml
```

Renders `templates/fleet-report-cronjob.yaml` only when `fleetReport.enabled`
**and** an API key exist. `SLACK_WEBHOOK_URL` is the existing `values.yaml:49`
env var — unchanged, and now serves both proactive paths.

`fleetReport.query`, when non-empty, replaces the `content:` of the prompt
ConfigMap entry; the API mounts it at `/etc/kubently/prompts/` where `get_prompt`
already looks. Changing it is a `helm upgrade` + pod restart, same as any prompt
change today.

`concurrencyPolicy: Forbid` and `successfulJobsHistoryLimit: 1` so a slow digest
never stacks on the next schedule.

## Not doing

- **No per-cluster Slack threading / message-per-cluster.** One message.
- **No digest state in Redis** (no "changed since yesterday" diffing). It is a
  snapshot. Diffing needs a retention story and an "is this the same problem"
  comparison; neither is worth it before anyone has read a digest.
- **No new API surface for scan results.** The digest exists in Slack and in the
  audit log (#55 will surface the latter); it is not a queryable object. Dry-run
  returns its answer to the caller and keeps nothing.
- **No per-cluster query overrides.** One query for the fleet. Per-cluster
  wording means a map in values and a merge order to explain; if someone needs
  cluster-specific checks, two CronJobs with different queries and explicit
  cluster lists already does it.
- **No dedup against Alertmanager.** A firing alert and the digest may both
  mention the same pod. Fixing that means correlating alert identity across two
  async paths — revisit if it actually annoys someone.

## Testing

- **Unit** (`tests/test_fleet_report.py`, import-light like `test_fleet.py`):
  Slack payload shape, the `SLACK_WEBHOOK_URL`-unset 503, that the endpoint
  returns 202 without awaiting diagnosis (mocked agent), and query resolution
  precedence — body `query` > `fleet_report.prompt.yaml` > built-in fallback.
- **kind E2E**: induce a failure (a deliberately broken Deployment), POST the
  endpoint, assert the Slack payload names the failing workload and its cluster.
  Mock the Slack endpoint with a local receiver rather than posting to a real
  workspace.
- **Dry-run E2E**: same induced failure, `dry_run: true`, assert a 200 with the
  answer **and** that the mock Slack receiver got nothing — a dry run that
  quietly posts is the one bug in this feature that burns a user's real channel.
- **Healthy-fleet case**: same E2E on a clean cluster asserts the short all-clear,
  which is the regression test for "the digest got chatty."
- **Scheduled path**: `kubectl create job --from=cronjob/...` in the kind E2E, so
  the CronJob's own wiring (image, API key mount, in-cluster URL) is covered and
  not just the endpoint.

## Acceptance criteria

From #54:

- [ ] Digest posts on schedule against a kind cluster with an induced failure
- [ ] Healthy-only fleet produces a short all-clear message

Added:

- [ ] `dry_run: true` returns the digest and posts nothing to Slack
- [ ] A `query` in the request body overrides the configured prompt for that call
- [ ] `fleetReport.query` in values changes the digest without code changes
- [ ] `kubectl create job --from=cronjob/kubently-fleet-report` runs a real digest
