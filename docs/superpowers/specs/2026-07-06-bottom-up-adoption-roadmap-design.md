# Bottom-Up Adoption Roadmap

**Date:** 2026-07-06
**Status:** Approved
**Goal:** Grow individual-engineer adoption (stars, installs, word-of-mouth). Every item is ranked by "does this help one engineer succeed alone in their first session, then show it off to a teammate."

## Context

Kubently today is strong on plumbing (A2A server, MCP server with `ask_kubently`, remote executor model, multi-cluster tokens, cloud auth, kubectl whitelist, LangSmith tracing, test automation) and thin on experience (time-to-value, distribution, shareable moments). Competitive moat vs kagent/k8sgpt/HolmesGPT is the intersection of agent-protocol-native (A2A + MCP) and the remote executor architecture.

Bottom-up adoption has two failure points this roadmap targets: the first 20 minutes (onboarding friction), and the absence of a moment worth sharing internally.

## Phase 1 — Own the first 20 minutes (`kubently init`)

**Deliverable:** one command from fresh laptop + kind cluster to answered diagnosis in under 5 minutes, zero YAML edits.

- New `kubently init` command in `kubently-cli`:
  - Creates namespace, generates the three secrets (Redis password, API key, executor token) — automating what CLAUDE.md documents as manual steps.
  - Runs `helm install` with bundled sane defaults against the current kubeconfig context.
  - Waits for pods ready, starts port-forward, drops into interactive chat.
- Published Helm repo (gh-pages via `chart-releaser` GitHub Action, one-time setup).
- Versioned GitHub releases with matching image tags.
- 30-second demo GIF at the top of README.

**Exit criterion:** timed cold-start run meets the 5-minute bar.

## Phase 2 — Ride the MCP wave (distribution, not code)

**Deliverable:** engineers already using Claude Code / Cursor get Kubently as a tool, not a new app.

- `kubently mcp` subcommand: local stdio↔HTTP bridge so `claude mcp add kubently -- kubently mcp` works without the user knowing the deployed endpoint. (MCP clients overwhelmingly assume local stdio servers; remote-HTTP-only loses most of the funnel.)
- Listings on MCP registries (official registry, Smithery).
- Two-line "add to Claude Code / Cursor" section in README.

The `ask_kubently` tool already exists — this phase is packaging.

## Phase 3 — The shareable moment (proactive diagnosis)

**Deliverable:** "the bot diagnosed the alert before I opened my laptop."

- Alertmanager webhook receiver endpoint on the API that triggers a diagnosis session.
- Result posted via Slack incoming-webhook (config-only for the user; no Slack app review process).
- Full Slack bot deferred until this proves out.

Sequenced last deliberately: proactive mode generates demand, and demand crashes into whatever friction Phases 1–2 haven't removed.

## Deliberately deferred (top-down / later)

- Audit UI and exportable audit trail (Redis `auth:audit` exists, unsurfaced).
- Per-user RBAC impersonation / multi-tenancy.
- Fleet-native cross-cluster queries (moat-defending, org-gate feature).
- Prometheus/Loki as diagnosis data sources (quality win, post-door).
- Any web UI — MCP clients are the UI.

## Sequencing rationale

Phases 1–2 are almost entirely packaging debt, not novel engineering; the adoption bottleneck is not where the interesting code is. Each phase gets its own spec → plan → implementation cycle; this document is the umbrella.
