# MCP Distribution (Roadmap Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Engineers already using Claude Code / Cursor get Kubently as a tool in two lines — via native HTTP transport or a `kubently mcp` stdio bridge — plus registry manifests so it's discoverable.

**Architecture:** The server side already exists (`ask_kubently` tool at `/mcp/`, streamable HTTP, `X-API-Key` auth — see docs/MCP.md). This phase is packaging: (1) a `kubently mcp` CLI subcommand that bridges stdio↔HTTP by delegating to the community-standard `mcp-remote` proxy via `npx` (no new bundled dependency, no hand-rolled protocol code), reading URL/key from the existing `~/.kubently/config.json`; (2) docs showing both connect paths; (3) a `server.json` manifest for the official MCP registry with submission instructions.

**Tech Stack:** TypeScript ESM CLI (commander), `npx -y mcp-remote` at runtime, jest (configured in Phase 1).

**Key repo facts** (verified 2026-07-06):
- MCP endpoint: `/mcp/` — trailing slash REQUIRED (bare `/mcp` 307-redirects and some clients drop the method); `X-API-Key` header auth (docs/MCP.md).
- `Config` (kubently-cli/nodejs/src/lib/config.ts) has `getApiUrl()` / `getApiKey()`, both `string | undefined`.
- CLI is at 2.2.0 after Phase 1; commands live in `src/commands/`, registered in `src/index.ts`; pure helpers pattern + tests established in `src/lib/installer.ts` / `.test.ts`.

---

### Task 1: `kubently mcp` stdio bridge (TDD on the pure part)

**Files:**
- Create: `kubently-cli/nodejs/src/commands/mcp.ts`
- Modify: `kubently-cli/nodejs/src/lib/installer.test.ts` → no; test goes in a new `kubently-cli/nodejs/src/commands/mcp.test.ts`
- Modify: `kubently-cli/nodejs/src/index.ts` (register)
- Modify: `kubently-cli/nodejs/package.json` (version 2.2.0 → 2.3.0)

- [ ] **Step 1: Write the failing test**

Create `kubently-cli/nodejs/src/commands/mcp.test.ts`:

```ts
import { describe, it, expect } from '@jest/globals';
import { buildMcpRemoteArgs, mcpUrl } from './mcp.js';

describe('mcpUrl', () => {
  it('appends /mcp/ with exactly one slash (trailing slash required by server)', () => {
    expect(mcpUrl('http://localhost:8080')).toBe('http://localhost:8080/mcp/');
    expect(mcpUrl('http://localhost:8080/')).toBe('http://localhost:8080/mcp/');
    expect(mcpUrl('https://kubently.example.com')).toBe('https://kubently.example.com/mcp/');
  });
});

describe('buildMcpRemoteArgs', () => {
  it('builds the npx mcp-remote invocation with auth header and http-only transport', () => {
    expect(buildMcpRemoteArgs('http://localhost:8080', 'k3y')).toEqual([
      '-y',
      'mcp-remote',
      'http://localhost:8080/mcp/',
      '--header',
      'X-API-Key:k3y',
      '--transport',
      'http-only',
    ]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kubently-cli/nodejs && npx jest src/commands/mcp.test.ts`
Expected: FAIL — cannot resolve `./mcp.js`.

- [ ] **Step 3: Implement the command**

Create `kubently-cli/nodejs/src/commands/mcp.ts`:

```ts
import { spawn } from 'node:child_process';
import { Command } from 'commander';
import { Config } from '../lib/config.js';

/** /mcp/ with the trailing slash — bare /mcp 307-redirects and some clients drop the method. */
export function mcpUrl(apiUrl: string): string {
  return `${apiUrl.replace(/\/+$/, '')}/mcp/`;
}

export function buildMcpRemoteArgs(apiUrl: string, apiKey: string): string[] {
  // ponytail: delegate stdio<->streamable-HTTP proxying to the community-standard
  // mcp-remote instead of hand-rolling protocol code; npx caches it after first run
  return [
    '-y',
    'mcp-remote',
    mcpUrl(apiUrl),
    '--header',
    `X-API-Key:${apiKey}`,
    '--transport',
    'http-only',
  ];
}

export function mcpCommand(config: Config): Command {
  const cmd = new Command('mcp');

  cmd
    .description('🔌 Run a local stdio MCP bridge to the Kubently API (for MCP clients)')
    .option('--api-url <url>', 'Kubently API URL (default: from ~/.kubently/config.json)')
    .option('--api-key <key>', 'API key (default: from ~/.kubently/config.json)')
    .action((opts) => {
      const apiUrl = opts.apiUrl ?? config.getApiUrl();
      const apiKey = opts.apiKey ?? config.getApiKey();
      if (!apiUrl || !apiKey) {
        // stderr only — stdout belongs to the MCP protocol
        console.error(
          'kubently mcp: missing API URL or key. Run "kubently install" or "kubently init" first, or pass --api-url/--api-key.'
        );
        process.exit(1);
      }
      const child = spawn('npx', buildMcpRemoteArgs(apiUrl, apiKey), { stdio: 'inherit' });
      child.on('exit', (code) => process.exit(code ?? 1));
    });

  return cmd;
}
```

**Why stderr matters:** an MCP client owns the stdio channel; any stray stdout line corrupts the JSON-RPC stream. No banner, no chalk, nothing on stdout from this command.

- [ ] **Step 4: Run test to verify it passes**

Run: `npx jest src/commands/mcp.test.ts`
Expected: PASS.

- [ ] **Step 5: Register + version bump + full verify**

In `src/index.ts` add `import { mcpCommand } from './commands/mcp.js';` next to the other command imports, and `program.addCommand(mcpCommand(config));` after the install command registration.

**Banner guard:** `src/index.ts` prints a figlet banner in a `preAction` hook for main commands. Verify the hook only fires for `thisCommand.name() === 'kubently'`… it does, but the top-level `showBanner()` on bare invocation isn't hit for subcommands. Confirm by running `node dist/index.js mcp --api-url http://x --api-key y` and checking NOTHING prints to stdout before mcp-remote output (banner would corrupt the protocol). If the banner appears, gate it: skip when `process.argv[2] === 'mcp'`.

In `package.json`: `"version": "2.2.0"` → `"version": "2.3.0"`.

Run: `npm run build && npx jest && node dist/index.js mcp --help`
Expected: build clean, all tests pass, help shows `--api-url`/`--api-key`.

- [ ] **Step 6: Commit**

```bash
git add kubently-cli/nodejs/src/commands/mcp.ts kubently-cli/nodejs/src/commands/mcp.test.ts kubently-cli/nodejs/src/index.ts kubently-cli/nodejs/package.json
git commit -m "feat(cli): kubently mcp — stdio bridge to /mcp/ via mcp-remote"
```

---

### Task 2: Registry manifest (official MCP registry)

**Files:**
- Create: `server.json` (repo root — where the registry publisher expects it)

- [ ] **Step 1: Write the manifest**

Create `server.json`:

```json
{
  "$schema": "https://static.modelcontextprotocol.io/schemas/2025-07-09/server.schema.json",
  "name": "io.github.kubently/kubently",
  "description": "Troubleshoot Kubernetes clusters agentically — natural-language cluster diagnosis via the ask_kubently tool",
  "repository": {
    "url": "https://github.com/kubently/kubently",
    "source": "github"
  },
  "version_detail": {
    "version": "2.3.0"
  },
  "packages": [
    {
      "registry_name": "npm",
      "name": "@kubently/cli",
      "version": "2.3.0",
      "runtime_hint": "npx",
      "package_arguments": [
        { "type": "positional", "value": "mcp" }
      ],
      "environment_variables": []
    }
  ],
  "remotes": [
    {
      "transport_type": "streamable-http",
      "url": "https://<your-kubently-host>/mcp/"
    }
  ]
}
```

Note: if the schema URL 404s or the current registry schema differs (it has churned), fetch the current schema from https://github.com/modelcontextprotocol/registry and adjust field names — keep the same content. Validation happens at `mcp-publisher publish` time anyway.

- [ ] **Step 2: Commit**

```bash
git add server.json
git commit -m "feat: MCP registry manifest (server.json)"
```

Actual submission (`mcp-publisher login github && mcp-publisher publish`) requires the user's GitHub auth interactively — listed as a follow-up for the user, not automated here. Same for Smithery (needs an account on smithery.ai; it can import from the official registry).

---

### Task 3: Docs — two-line connect for Claude Code / Cursor

**Files:**
- Modify: `README.md` (new "Use from Claude Code / Cursor (MCP)" section after the quickstart)
- Modify: `docs/MCP.md` (add the `kubently mcp` bridge + `claude mcp add` examples)
- Modify: `CHANGELOG.md`

- [ ] **Step 1: README section**

Insert after the quickstart section:

```markdown
### Use from Claude Code / Cursor (MCP)

Already ran `kubently install`? Add Kubently to Claude Code:

​```bash
claude mcp add kubently -- kubently mcp
​```

Or connect directly over HTTP (no bridge process):

​```bash
claude mcp add --transport http kubently http://localhost:8080/mcp/ \
  --header "X-API-Key: <your-api-key>"
​```

Then ask Claude things like *"use kubently to figure out why payments pods are
crashlooping"*. Any MCP client works — see [docs/MCP.md](docs/MCP.md) for
Cursor and generic configuration.
```

(Strip the `​` zero-width fence escapes.)

- [ ] **Step 2: docs/MCP.md — add bridge section**

Read docs/MCP.md and add a "Connecting with the Kubently CLI bridge" section alongside the existing client examples, showing `kubently mcp` (uses saved config), `--api-url`/`--api-key` overrides, and the `claude mcp add kubently -- kubently mcp` one-liner. Keep the existing HTTP examples — the bridge is for stdio-only clients.

- [ ] **Step 3: CHANGELOG entry** (top of file, under a `## [Unreleased] - <today>` heading if today's section exists, else create it)

```markdown
- **`kubently mcp`: stdio MCP bridge (CLI 2.3.0)** — `claude mcp add kubently -- kubently mcp`
  proxies stdio to the deployed `/mcp/` endpoint via mcp-remote, reading URL/key from CLI
  config. Plus `server.json` manifest for the official MCP registry and README/MCP.md
  connect guides.
```

- [ ] **Step 4: Commit**

```bash
git add README.md docs/MCP.md CHANGELOG.md
git commit -m "docs: two-line MCP connect for Claude Code/Cursor; changelog"
```

---

### Task 4: E2E verification against the kind cluster

No files. Uses the existing `kubently` kind cluster (context `kind-kubently`, namespace `kubently`).

- [ ] **Step 1: Port-forward and configure**

```bash
kubectl --context kind-kubently -n kubently port-forward svc/kubently-api 8080:8080 &
```

Get an API key for that cluster (from the `kubently-api-keys` secret: `kubectl --context kind-kubently -n kubently get secret kubently-api-keys -o jsonpath='{.data.keys}' | base64 -d | head -1`).

- [ ] **Step 2: Drive the bridge over stdio**

MCP stdio is newline-delimited JSON-RPC. Send an initialize + tools/list through the bridge:

```bash
cd kubently-cli/nodejs
printf '%s\n%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"e2e","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | timeout 60 node dist/index.js mcp --api-url http://localhost:8080 --api-key "<key>" 2>/dev/null
```

Expected: two JSON-RPC results on stdout — an `initialize` result (serverInfo) and a `tools/list` result containing `"ask_kubently"`. If mcp-remote exits before responding, drop `--transport http-only` and retry (transport flag names have churned across mcp-remote versions; adjust `buildMcpRemoteArgs` + test to whatever works).

- [ ] **Step 3: Kill the port-forward, record results**

```bash
pkill -f "port-forward.*kubently-api 8080"
```

Record the tools/list output in the PR description.

---

## Self-Review Notes

- **Spec coverage:** stdio bridge (Task 1), registry listing (Task 2 — manifest committed, submission is a user follow-up since it needs interactive GitHub auth), two-line docs (Task 3), verified against a real deployment (Task 4).
- **Known risk:** `mcp-remote` CLI flags (`--transport http-only`, `--header`) have churned across versions; Task 4 Step 2 includes the fallback procedure. The header uses `X-API-Key:value` (no space) because some mcp-remote versions split on the first colon only.
- **Deliberately skipped:** bundling mcp-remote as a dependency (npx is cached after first run; a lockstep dep adds maintenance for no UX gain), Smithery automation (account-gated).
