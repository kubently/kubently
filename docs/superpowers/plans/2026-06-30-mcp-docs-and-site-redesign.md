# MCP Docs + kubently.io Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document Kubently's MCP server as a first-class connection method, and redesign kubently.io into a committed "Terminal Noir" dark theme.

**Architecture:** Two independent streams. Stream 1 edits markdown docs in the `kubently` repo. Stream 2 rebuilds the Jekyll theme in the `kubently-site` repo (minima removed, custom dark CSS/layouts). No application code changes.

**Tech Stack:** Markdown; Jekyll 4 + GitHub Pages, SCSS, vanilla JS (typewriter), Rouge (dark syntax theme), Ruby 3.3.0.

**Verified facts (do not re-derive):**
- MCP endpoint: `/mcp`, **streamable HTTP** transport (FastMCP, `streamable_http_path="/"`).
- Auth: `X-API-Key` header (same key as CLI/A2A). 401 without a valid key.
- Enablement: auto-mounted at startup **when the `mcp` SDK is installed** (the `a2a` extra). No env flag. Logs `MCP server mounted at /mcp`.
- Tools: `list_clusters() -> list[str]`; `execute_kubectl(cluster_id, command, namespace="default") -> str` (command = kubectl without the leading `kubectl`, e.g. `get pods -o wide`).
- Internal wiring: adapters call `/debug/clusters` and `/debug/execute`; `KUBENTLY_API_URL` sets the internal API URL.

**Repo paths:**
- Stream 1: `/Users/adickinson/repos/kubently/.claude/worktrees/sleepy-ishizaka-d20640`
- Stream 2: `/Users/adickinson/repos/kubently-site`

---

## File Structure

**Stream 1 (docs, `kubently`):**
- Create: `docs/MCP.md` — the MCP connect guide (single source of truth for MCP usage).
- Modify: `docs/SYSTEM_DESIGN.md`, `docs/ARCHITECTURE.md`, `docs/AGENTGATEWAY_SETUP.md`, `docs/INDEX.md`, `README.md`, `CHANGELOG.md`.

**Stream 2 (site, `kubently-site`):**
- `assets/css/style.scss` — full custom dark stylesheet (no minima).
- `assets/css/syntax-dark.scss` *(new)* — dark Rouge highlight theme.
- `_layouts/default.html`, `_layouts/home.html`, `_layouts/page.html`.
- `_includes/head.html`, `header.html`, `footer.html`, `navigation.html`.
- `index.md` — rebuilt hero + sections incl. A2A/MCP connect block.
- `assets/js/main.js` — typewriter content + reveal + flow animation toggles.
- `_config.yml` — drop `theme: minima`.

---

# STREAM 1 — MCP DOCUMENTATION

### Task 1: Verify MCP facts against source

**Files:** Read-only — `kubently/modules/mcp/server.py`, `kubently/modules/mcp/tools.py`, `kubently/main.py` (mount region ~L150-195), `pyproject.toml`/`requirements*` (confirm the `a2a` extra pulls `mcp`).

- [ ] **Step 1:** Confirm tool names/signatures, the `/mcp` mount, `X-API-Key` enforcement, and the "SDK installed → mounted" behavior still match the Verified facts above. If anything differs, correct the facts block and any dependent task before writing prose.

### Task 2: Write `docs/MCP.md`

**Files:** Create `docs/MCP.md`.

- [ ] **Step 1:** Write the guide with these sections:
  - **Overview** — Kubently runs an optional MCP server so any MCP client (Claude Desktop, Cursor, custom agents) gets the same read-only multi-cluster troubleshooting the A2A agent has.
  - **Endpoint & transport** — streamable HTTP at `https://<your-kubently-host>/mcp`.
  - **Authentication** — `X-API-Key: <key>` header; 401 otherwise; same keys as `API_KEYS` / the CLI.
  - **Enabling it** — present whenever the `mcp` SDK is installed (the `a2a` extra); startup log line `MCP server mounted at /mcp`; no separate flag.
  - **Tools** — table: `list_clusters()` → cluster IDs; `execute_kubectl(cluster_id, command, namespace="default")` → command output; note read-only enforcement (executor whitelist + RBAC) and the `command` format.
  - **Connect a client** — a generic streamable-HTTP MCP client config (URL + `X-API-Key` header) and a Claude Desktop / Cursor example. *(Verify the exact client config shape before finalizing; if a remote-HTTP header config isn't supported by a given client, say so and show the supported path.)*
  - **Relationship to A2A** — both share auth/session/queue; A2A = full agent over `/a2a/`, MCP = tools for any MCP client over `/mcp`.
- [ ] **Step 2 (verify):** `grep -n "create_debug_session\|get_command_result\|close_session" docs/MCP.md` → expect **no matches** (no stale tool names). Confirm `list_clusters` and `execute_kubectl` both appear.

### Task 3: Fix stale MCP tool names in design docs

**Files:** Modify `docs/SYSTEM_DESIGN.md` ("MCP Tool Exposure"), `docs/ARCHITECTURE.md` ("Multi-Agent System (MAS) Integration").

- [ ] **Step 1:** Replace stale tool names (`create_debug_session`, `execute_kubectl`(old sig), `get_command_result`, `close_session`) with the real `list_clusters` / `execute_kubectl(cluster_id, command, namespace)` and the `/mcp` streamable-HTTP + `X-API-Key` facts. Link to `MCP.md` for detail.
- [ ] **Step 2 (verify):** `grep -rn "create_debug_session\|get_command_result\|close_session" docs/` → expect **no matches**.

### Task 4: Surface MCP in entry-point docs

**Files:** Modify `docs/AGENTGATEWAY_SETUP.md`, `docs/INDEX.md`, `README.md`.

- [ ] **Step 1:** `AGENTGATEWAY_SETUP.md` — expand the one-line MCP mention into a short paragraph linking to `MCP.md`.
- [ ] **Step 2:** `INDEX.md` — add `MCP.md` to the documentation index.
- [ ] **Step 3:** `README.md` — in the connect/usage area, add MCP next to A2A as a supported way to connect, linking to `docs/MCP.md`.
- [ ] **Step 4 (verify):** `grep -rln "MCP.md" docs/INDEX.md README.md docs/AGENTGATEWAY_SETUP.md` → all three present.

### Task 5: Changelog + commit (Stream 1)

**Files:** Modify `CHANGELOG.md`.

- [ ] **Step 1:** Add an entry: MCP server documented (`docs/MCP.md`); stale MCP tool names corrected; MCP surfaced in README/INDEX.
- [ ] **Step 2 (commit — only when the user approves committing):**
  ```bash
  cd /Users/adickinson/repos/kubently/.claude/worktrees/sleepy-ishizaka-d20640
  gcmwm "docs: document MCP server and fix stale MCP tool references"
  ```

---

# STREAM 2 — kubently.io REDESIGN (Terminal Noir)

> Build order: foundation (tokens + shell) → homepage sections → inner-page theme → verify.
> Verification is **build + visual**, not unit tests: run `bundle exec jekyll serve` and
> screenshot. Commit after each task group.

**Design tokens (use as SCSS/CSS variables):** bg `#0a0c10`, bg-deep `#070a0e`, surface `#0d1117`, surface-2 `#15191f`, border `#1b222b`, border-2 `#2c333d`, text `#e6edf3`, text-muted `#8b97a5`, accent `#2dd4bf`, accent-2 `#22d3ee`, ok `#56d364`, warn `#e3b341`. Fonts: Inter (UI), JetBrains Mono (mono).

### Task 6: Branch + strip minima foundation

**Files:** `_config.yml`, `assets/css/style.scss`, `_layouts/default.html`, `_includes/head.html`.

- [ ] **Step 1:** From `~/repos/kubently-site`, create a branch: `git checkout -b redesign-terminal-noir`.
- [ ] **Step 2:** `style.scss` — remove `@import "minima";`. Add `:root` design tokens above + a base reset, `body` (dark bg, Inter, `color-scheme: dark`), link/selection styles, and a `prefers-reduced-motion` block that disables animations.
- [ ] **Step 3:** `_config.yml` — remove/neutralize `theme: minima` (keep plugins, nav, seo). 
- [ ] **Step 4:** `_layouts/default.html` — own the full HTML shell: `<!DOCTYPE html>`, `{% include head.html %}`, `{% include header.html %}`, `<main>{{ content }}</main>`, `{% include footer.html %}`, scripts. (Previously inherited from minima.)
- [ ] **Step 5:** `_includes/head.html` — meta, SEO tag, fonts preconnect + Inter/JetBrains Mono, `style.scss` link, favicon/logo, theme-color `#0a0c10`.
- [ ] **Step 6 (verify):** `cd ~/repos/kubently-site && bundle exec jekyll build 2>&1 | tail -5` → builds with **no minima errors**. `grep -rn "minima" _config.yml assets/css/style.scss` → no active import.
- [ ] **Step 7 (commit):** `git add -A && git commit -m "redesign: strip minima, add dark foundation + custom shell"`

### Task 7: Header, footer, nav (dark)

**Files:** `_includes/header.html`, `footer.html`, `navigation.html`, `style.scss`.

- [ ] **Step 1:** `header.html` — sticky dark header: logo (existing `kubently.svg`/cropped logo), nav links from `_config.yml` `navigation`, a GitHub button. Mobile hamburger (CSS/JS toggle).
- [ ] **Step 2:** `navigation.html` — render `site.navigation` incl. the Guides children as a dropdown.
- [ ] **Step 3:** `footer.html` — dark footer: tagline, GitHub/docs links, license note.
- [ ] **Step 4:** `style.scss` — component styles for header (blur, border-bottom, sticky), nav hover/active, dropdown, footer, mobile menu.
- [ ] **Step 5 (verify):** serve locally; header sticky + nav dropdown + mobile menu work; footer dark. Screenshot.
- [ ] **Step 6 (commit):** `git commit -am "redesign: dark header, nav, footer"`

### Task 8: Homepage hero (live terminal)

**Files:** `index.md`, `style.scss`, `assets/js/main.js`.

- [ ] **Step 1:** `index.md` hero block — two columns: left = mono kicker (`// Kubernetes, agentically`), headline "Debug clusters by **talking to them**", subtitle (mentions A2A **and** MCP), CTAs (Get started / GitHub), shield badges; right = terminal window (chrome dots + title + `#typewriter` body).
- [ ] **Step 2:** `style.scss` — hero layout/grid, one soft radial accent glow behind hero (replaces the 3 aurora orbs), terminal window styling, blinking cursor, responsive single-column < 640px.
- [ ] **Step 3:** `main.js` — set the typewriter script lines to a realistic Kubently troubleshooting session (the prod-EU CrashLoopBackOff → missing secret root-cause flow from the mockup); keep existing reveal-on-scroll.
- [ ] **Step 4 (verify):** serve; typewriter animates; reduced-motion shows static final text; mobile collapses cleanly. Screenshot hero.
- [ ] **Step 5 (commit):** `git commit -am "redesign: terminal-noir hero with live debug terminal"`

### Task 9: Connect section (A2A + MCP) — the MCP story

**Files:** `index.md`, `style.scss`.

- [ ] **Step 1:** `index.md` — "Speaks your agent's language" section, two cards:
  - **A2A** — full agent over `/a2a/`; tiny `curl` snippet (message/stream) + `X-API-Key`.
  - **MCP** — tools for any MCP client over `/mcp` (streamable HTTP, `X-API-Key`); tiny client-config snippet; "New" tag; link to docs `MCP.md`.
- [ ] **Step 2:** `style.scss` — two-card grid, code-snippet styling (mono, surface bg, accent prompt), card hover, "New" badge.
- [ ] **Step 3 (verify):** serve; both cards render with readable snippets; mobile stacks. Screenshot.
- [ ] **Step 4 (commit):** `git commit -am "redesign: A2A + MCP connect section"`

### Task 10: "How it works" animated flow + features + use cases

**Files:** `index.md`, `style.scss`.

- [ ] **Step 1:** `index.md` — "How it works": `Agent → Kubently API → Executor → Cluster` flow with animated packets and captions (SSE / read-only / multi-cluster). Reuse the mockup's flow markup.
- [ ] **Step 2:** `index.md` — Features grid (real-time SSE, read-only/RBAC, multi-LLM, simple deploy, autoscale, flexible integration) and Use cases (intelligent troubleshooting / multi-agent / enterprise) — replace emoji with inline-SVG line icons in accent color.
- [ ] **Step 3:** `style.scss` — flow nodes/arrows + packet keyframes (reduced-motion safe), feature/use-case card grid, icon sizing.
- [ ] **Step 4 (verify):** serve; flow animates; cards align on desktop + mobile. Screenshot full homepage.
- [ ] **Step 5 (commit):** `git commit -am "redesign: how-it-works flow, features, use cases"`

### Task 11: Inner-page (docs) theme + dark syntax highlighting

**Files:** `_layouts/page.html`, `assets/css/syntax-dark.scss` (new), `style.scss`, `_includes/head.html`.

- [ ] **Step 1:** Generate/author a dark Rouge theme into `assets/css/syntax-dark.scss` (e.g. `bundle exec rougify style monokai.sublime > assets/css/syntax-dark.css` then adapt, or hand-write tokens to match palette). Link it from `head.html`.
- [ ] **Step 2:** `_layouts/page.html` — long-form dark layout: constrained measure (~72ch), page title header, optional in-page TOC, `.page-content` prose.
- [ ] **Step 3:** `style.scss` — `.page-content` typography for dark: headings, paragraphs, lists, links, blockquotes, **tables**, inline code, fenced code blocks (surface bg + border), images. Ensure WCAG-AA contrast.
- [ ] **Step 4 (verify):** serve and open **each** inner page — `/installation/`, `/guides/`, `/api/`, `/architecture/`, `/contributing/` — confirm readable dark long-form and **styled** code blocks (no unstyled white blocks). Screenshot `/api/` (code-heavy) and `/architecture/`.
- [ ] **Step 5 (commit):** `git commit -am "redesign: dark docs/page theme + syntax highlighting"`

### Task 12: Cross-cutting polish + full verification

**Files:** `style.scss`, any page as needed.

- [ ] **Step 1:** Focus-visible states on all interactive elements; scrollbar styling; 404 page if present; check `og:image`/SEO still set.
- [ ] **Step 2 (verify — build):** `bundle exec jekyll build 2>&1 | tail -5` → clean build, no warnings about missing minima includes/classes. `grep -rn "minima" _layouts _includes assets _config.yml` → no active references.
- [ ] **Step 3 (verify — visual):** Playwright/screenshot pass over homepage + all 5 inner pages at desktop (1280) and mobile (390) widths; confirm no layout breaks, no unstyled regions, contrast OK.
- [ ] **Step 4 (commit):** `git commit -am "redesign: polish, a11y, final verification"`

### Task 13: Hand off site for review

- [ ] **Step 1:** Summarize screenshots + the branch; do **not** push or open a PR until the user asks. Confirm whether the typewriter/flow copy and the MCP snippet are accurate before any deploy to `kubently.io`.

---

## Self-Review Notes

- **Spec coverage:** Stream 1 tasks 2-5 cover every doc in the spec table (`MCP.md`, SYSTEM_DESIGN, ARCHITECTURE, AGENTGATEWAY_SETUP, README, INDEX, CHANGELOG; CLAUDE.md note is optional/low-priority and intentionally dropped to stay lazy). Stream 2 tasks 6-12 cover all spec files (style.scss, syntax theme, all layouts/includes, index.md, main.js, _config.yml) and both risks (minima base reset → Task 6/11; Rouge CSS → Task 11). Dark-only (no light toggle) honored.
- **Verification fit:** A redesign has no meaningful unit tests; gates are `jekyll build` + per-page screenshots, which is the correct check for this work.
- **Commits:** gated on user approval per the user's "commit only when asked" rule.
