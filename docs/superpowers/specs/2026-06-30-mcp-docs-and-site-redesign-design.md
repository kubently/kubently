# MCP Docs Update + kubently.io Site Redesign — Design

**Date:** 2026-06-30
**Status:** Awaiting review

## Overview

Two related deliverables:

1. **Docs (this repo, `kubently/`)** — update documentation to reflect the now-shipped
   **MCP server** so MCP is a documented, first-class way to connect to Kubently
   (alongside the A2A protocol).
2. **Site redesign (`~/repos/kubently-site/`)** — redesign kubently.io from a templated
   docs-site look into a committed **"Terminal Noir"** dark aesthetic that signals a serious
   project, and surface MCP in the connect story.

These are independent and can ship separately.

---

## Stream 1 — MCP Documentation (repo: `kubently`)

### What MCP actually is (verified facts to document)

- Implemented at `kubently/modules/mcp/server.py` + `kubently/modules/mcp/tools.py`,
  mounted in `kubently/main.py` at `/mcp`.
- Built on **FastMCP** (official Python MCP SDK). Conditionally mounted only when the MCP
  SDK is installed (via the `a2a` extra).
- **Auth:** same `X-API-Key` header as the CLI / A2A, enforced by the shared
  `add_api_key_auth()` ASGI wrapper. 401 without a valid key.
- **Tools exposed:** `list_clusters()` and
  `execute_kubectl(cluster_id, command, namespace="default")` — thin adapters over the
  existing `/debug/clusters` and `/debug/execute` endpoints. Read-only enforced downstream
  by executor whitelist + Kubernetes RBAC.

> Implementation details above are from exploration and **will be re-verified against the
> source before any doc text is written** (exact tool names, signatures, enable flag).

### Changes

| File | Change |
|------|--------|
| `docs/MCP.md` *(new)* | Dedicated MCP guide: what it is, `/mcp` endpoint, X-API-Key auth, the two tools, and copy-paste connect configs for Claude Desktop / Cursor / generic MCP clients. |
| `docs/SYSTEM_DESIGN.md` | Fix the "MCP Tool Exposure" section — replace stale tool names (`create_debug_session`, `get_command_result`, `close_session`) with the real `list_clusters` / `execute_kubectl`. |
| `docs/ARCHITECTURE.md` | Refresh "Multi-Agent System (MAS) Integration" MCP signatures to match. |
| `docs/AGENTGATEWAY_SETUP.md` | Expand the one-line MCP mention into a real reference (link to `MCP.md`). |
| `README.md` | Add MCP alongside A2A as a connection method; link to `docs/MCP.md`. |
| `docs/INDEX.md` | Add `MCP.md` to the index. |
| `CHANGELOG.md` | Record the MCP server + docs (project rule: maintain changelog). |
| `CLAUDE.md` | Short note on how to run/test the MCP server (optional, low priority). |

### Out of scope (Stream 1)

- No changes to MCP implementation code — docs only.

---

## Stream 2 — Site Redesign (repo: `kubently-site`)

### Decisions locked

- **Direction:** Terminal Noir — dark, monospace-accented; the live debug session is the hero.
- **Scope:** Full theme — homepage **and** all inner pages (installation, guides, api,
  architecture, contributing) restyled to match.
- **Toolchain:** Keep **Jekyll + GitHub Pages** (deploy via `.github/workflows/jekyll.yml`
  already works; content stays in markdown). **Drop `@import "minima"`** and own the
  layouts + CSS fully — minima is the root cause of the current "templated" feel.

### Art direction

- **Palette (dark-first):** page `#0a0c10` / deepest `#070a0e`; surfaces `#0d1117`,
  `#15191f`; borders `#1b222b`, `#2c333d`; text `#e6edf3`, muted `#8b97a5`; primary accent
  **teal/mint `#2dd4bf`**, secondary cyan `#22d3ee`. Teal matches the existing logo, so
  branding stays coherent. Status colors for terminal: green `#56d364`, amber `#e3b341`.
- **Type:** Inter (UI) + JetBrains Mono (terminal, code, kicker labels) — both already loaded.
- **Motion (subtle, all respect `prefers-reduced-motion`):** typewriter terminal (reuse
  existing `main.js`), blinking cursor, animated packets along the architecture flow,
  reveal-on-scroll (already present), one soft radial glow behind the hero (replaces the
  three floating aurora orbs, which read as generic).
- **Icons:** replace emoji feature icons with a consistent inline-SVG line-icon set in the
  accent color.

### Homepage structure

1. Sticky dark header — logo, nav, GitHub button.
2. **Hero** — left: kicker + headline ("Debug clusters by talking to them") + subtitle +
   CTAs + badges; right: live typewriter **terminal** running a real troubleshooting session.
3. **Connect section ("Speaks your agent's language")** — two cards, **A2A** and **MCP**,
   each with a tiny code snippet (A2A: curl to `/a2a/`; MCP: client config JSON for `/mcp`).
   This is where MCP gets first-class billing on the site.
4. **How it works** — the animated `Agent → Kubently API → Executor → Cluster` flow with
   SSE / read-only / multi-cluster captions (the element borrowed from direction C).
5. **Features** grid — line icons: real-time SSE, read-only/RBAC, multi-LLM, simple deploy,
   autoscale, flexible integration.
6. **Use cases** — intelligent troubleshooting / multi-agent systems / enterprise-ready.
7. **Quick start** — short terminal code block (install + first run).
8. CTA band + footer.

### Files to own (replacing minima)

| File | Change |
|------|--------|
| `assets/css/style.scss` | Remove `@import "minima"`; write the full dark stylesheet (base reset, typography, layout, components, long-form `.page-content` prose, responsive, reduced-motion). The big one. |
| `_layouts/default.html` | Full HTML shell — no longer inherits minima's default. |
| `_includes/head.html`, `header.html`, `footer.html`, `navigation.html` | Rewrite for the dark theme + nav from `_config.yml`. |
| `_layouts/home.html`, `_layouts/page.html` | Restyle; `page` gets readable dark long-form layout for the markdown docs. |
| `index.md` | Rebuild hero + sections per structure above; add the Connect (A2A + MCP) section. |
| `assets/css/` syntax theme | Add a **dark Rouge** highlight theme (minima previously supplied highlight CSS; dropping it means code blocks lose styling otherwise). |
| `_config.yml` | Minor: ensure `theme:` no longer points at minima if that breaks the build; keep nav. |

### Edge cases / risks

- Dropping minima removes its base CSS reset and the classes markdown pages rely on →
  the new stylesheet must provide a complete typographic base for `.page-content`.
- Rouge syntax-highlight CSS must be replaced or code blocks render unstyled.
- Inner doc pages are long-form; dark long-form needs deliberate contrast + line-length
  limits to stay readable.

### Out of scope (Stream 2)

- **Light-mode toggle** — ship dark-only. *(ponytail: skipped; add a toggle later if asked.)*
- No content rewrites of the doc pages themselves beyond what the MCP story needs.
- No toolchain migration (staying on Jekyll).

---

## Verification

- **Docs:** re-read MCP source before writing; confirm tool names/signatures/auth/enable
  flag match the prose.
- **Site:** build locally with `bundle exec jekyll serve` (Ruby 3.3.0 per `.tool-versions`);
  confirm homepage + each inner page render with no minima references and no unstyled code
  blocks; screenshot key pages (Playwright) to confirm the look.

## Commit / delivery

- Each repo committed separately. The site repo follows the user's normal git flow; nothing
  is committed or pushed until the user asks.
