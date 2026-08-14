# Fleet-First Roadmap (2026-08 refresh)

**Date:** 2026-08-14
**Status:** Approved
**Supersedes:** `2026-07-06-bottom-up-adoption-roadmap-design.md` (all three phases shipped: `kubently install`, MCP bridge/registry, Alertmanager→Slack proactive diagnosis)

## Strategic frame

Kubently's defensible position is **free, self-hosted, vendor-neutral multi-cluster
troubleshooting via outbound executors** — reaching clusters you can't (no inbound
ingress, no shared kubeconfig). Competitors either paywall multi-cluster (kagent /
Solo Enterprise) or don't do it at all (k8sgpt, HolmesGPT, kubectl-ai). Every
increment below either builds that story or hardens it. A2A/MCP support is
table-stakes plumbing, not the pitch.

Work ships in small independent increments, each demoable on its own. Each
increment is tracked as a GitHub issue; each gets its own spec → plan →
implementation cycle when picked up.

## Increments (priority order)

1. **Fleet fan-out** — `execute_kubectl_multi`: one question answered across all
   registered clusters in parallel. The headline capability; currently the agent
   queries clusters sequentially one call at a time. Ships with README
   repositioning + fleet demo GIF. Spec: `2026-08-14-fleet-fanout-design.md`.
2. **Fleet health report** — scheduled fleet-wide scan posting an AI-diagnosed
   digest to Slack. Builds on the shipped webhook module + fan-out.
3. **Audit surfacing** — `kubently audit`: list/export every command the agent
   ran (the Redis `auth:audit` log exists but is unsurfaced). The governance
   answer to "you let an AI run kubectl where?"
4. **Prometheus/Loki diagnosis sources** — agent correlates metrics and logs,
   not just kubectl output. Biggest diagnosis-quality jump.
5. **mTLS / per-executor certificate identity** — replace Redis bearer tokens
   with zero-trust executor identity. Hardening for security-sensitive deploys.

## Deliberately deferred

- Web UI (MCP clients are the UI), per-user RBAC impersonation, full Slack bot
  (incoming webhooks suffice until proven out).
