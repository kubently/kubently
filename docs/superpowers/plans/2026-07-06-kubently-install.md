# `kubently install` One-Command Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One command (`kubently install`) takes a fresh cluster from nothing to an answered diagnosis chat in under 5 minutes, plus a published Helm repo and README quickstart.

**Architecture:** New `install` command in the existing Node.js CLI (`kubently-cli/nodejs`, commander + inquirer + ora). It automates what `deployment/scripts/kind-e2e.sh` does by hand: namespace + three secrets, `helm upgrade --install` from the published chart repo, port-forward, seed the executor token via the admin API (`POST /admin/agents/{id}/token` with a custom token — the glue no Helm template does), then saves CLI config and drops into the existing `runDebugSession` chat. Pure logic lives in `src/lib/installer.ts` (unit-tested); shell glue is thin `spawnSync` wrappers verified by a manual kind run. Chart publishing uses `helm/chart-releaser-action` to gh-pages.

**Tech Stack:** TypeScript ESM, commander, inquirer, ora, axios, js-yaml (all existing deps — nothing new), jest + ts-jest (installed but unconfigured), helm/chart-releaser-action.

**Spec deviation (approved direction):** the spec says `kubently init`, but `init` already exists and means "configure CLI → existing API". The new command is `kubently install` to avoid breaking existing semantics.

**Key repo facts** (verified 2026-07-06):
- Chart at `deployment/helm/kubently`, name `kubently`, version 1.0.0, image defaults already `ghcr.io/kubently/kubently{,-executor}:latest`.
- `templates/api-deployment.yaml` hardcodes env refs to secret `kubently-llm-secrets`; chart default `redis.auth.existingSecret` is `kubently-redis-password`; API keys secret referenced via `--set api.existingSecret=kubently-api-keys`.
- Executor auth: token passed via `--set executor.token=...` AND must exist in Redis as `executor:token:{clusterId}`. The admin API (`KubentlyAdminClient.createAgentToken(clusterId, customToken)`) writes it — then the executor deployment needs a rollout restart to re-auth.
- CLI has `runDebugSession(apiUrl, apiKey?, clusterId?, insecure?)` exported from `src/commands/debug.ts` and `KubentlyAdminClient` in `src/lib/adminClient.ts` (`ClusterListItem` has fields `id`, `connected`).
- `package.json` has `"test": "jest"` and jest 30 + ts-jest installed, but **no jest config and zero test files** — Task 1 fixes that.
- Project is `"type": "module"` with ESM imports ending in `.js` — jest needs the moduleNameMapper recipe below.

---

### Task 1: Make `npm test` work (jest config)

**Files:**
- Create: `kubently-cli/nodejs/jest.config.cjs`
- Modify: `kubently-cli/nodejs/tsconfig.json` (exclude test files from build)

- [ ] **Step 1: Write jest config**

Create `kubently-cli/nodejs/jest.config.cjs`:

```js
/** ts-jest transpiles TS tests to CJS; moduleNameMapper strips the ESM ".js"
 * suffix this codebase uses on relative imports so jest can resolve the .ts source. */
module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  roots: ['<rootDir>/src'],
  moduleNameMapper: {
    '^(\\.{1,2}/.*)\\.js$': '$1',
  },
  transform: {
    '^.+\\.ts$': ['ts-jest', { tsconfig: { module: 'commonjs' } }],
  },
};
```

- [ ] **Step 2: Exclude tests from the build**

In `kubently-cli/nodejs/tsconfig.json`, add (or extend) the top-level `exclude` array so `dist/` never contains test files:

```json
"exclude": ["node_modules", "dist", "src/**/*.test.ts"]
```

(If an `exclude` key already exists, append `"src/**/*.test.ts"` to it.)

- [ ] **Step 3: Verify jest runs**

Run: `cd kubently-cli/nodejs && npx jest --passWithNoTests`
Expected: `No tests found, exiting with code 0` (success).

Run: `cd kubently-cli/nodejs && npm run build`
Expected: compiles cleanly (proves tsconfig edit didn't break the build).

- [ ] **Step 4: Commit**

```bash
git add kubently-cli/nodejs/jest.config.cjs kubently-cli/nodejs/tsconfig.json
git commit -m "test(cli): configure jest for ESM TypeScript"
```

---

### Task 2: Pure install helpers (TDD)

**Files:**
- Create: `kubently-cli/nodejs/src/lib/installer.ts`
- Test: `kubently-cli/nodejs/src/lib/installer.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `kubently-cli/nodejs/src/lib/installer.test.ts`:

```ts
import {
  genToken,
  providerEnvVar,
  sanitizeClusterId,
  buildHelmArgs,
  secretManifest,
  HELM_REPO_URL,
} from './installer.js';

describe('genToken', () => {
  it('generates hex of the requested byte length', () => {
    expect(genToken(32)).toMatch(/^[0-9a-f]{64}$/);
    expect(genToken(16)).toMatch(/^[0-9a-f]{32}$/);
  });

  it('generates unique values', () => {
    expect(genToken()).not.toEqual(genToken());
  });
});

describe('providerEnvVar', () => {
  it('maps each supported provider to its env var', () => {
    expect(providerEnvVar('anthropic-claude')).toBe('ANTHROPIC_API_KEY');
    expect(providerEnvVar('openai')).toBe('OPENAI_API_KEY');
    expect(providerEnvVar('google-gemini')).toBe('GOOGLE_API_KEY');
  });

  it('throws on unknown provider', () => {
    expect(() => providerEnvVar('bedrock')).toThrow(/Unknown LLM provider/);
  });
});

describe('sanitizeClusterId', () => {
  it('lowercases and replaces invalid chars with hyphens', () => {
    expect(sanitizeClusterId('kind-kubently')).toBe('kind-kubently');
    expect(sanitizeClusterId('gke_my-proj_us-central1_prod')).toBe('gke-my-proj-us-central1-prod');
    expect(sanitizeClusterId('Docker Desktop')).toBe('docker-desktop');
  });

  it('strips leading/trailing hyphens and falls back to "default"', () => {
    expect(sanitizeClusterId('--weird--')).toBe('weird');
    expect(sanitizeClusterId('___')).toBe('default');
  });
});

describe('buildHelmArgs', () => {
  const base = {
    namespace: 'kubently',
    clusterId: 'kind-kubently',
    executorToken: 'tok',
    provider: 'anthropic-claude',
  };

  it('uses the published repo when no local chart path is given', () => {
    const args = buildHelmArgs(base);
    expect(args.slice(0, 4)).toEqual(['upgrade', '--install', 'kubently', 'kubently']);
    expect(args).toContain('--repo');
    expect(args).toContain(HELM_REPO_URL);
  });

  it('uses a local chart path when given', () => {
    const args = buildHelmArgs({ ...base, chartPath: './deployment/helm/kubently' });
    expect(args.slice(0, 4)).toEqual(['upgrade', '--install', 'kubently', './deployment/helm/kubently']);
    expect(args).not.toContain('--repo');
  });

  it('wires executor, api-keys secret, provider, and waits', () => {
    const args = buildHelmArgs(base);
    expect(args).toContain('api.existingSecret=kubently-api-keys');
    expect(args).toContain('api.env.LLM_PROVIDER=anthropic-claude');
    expect(args).toContain('executor.enabled=true');
    expect(args).toContain('executor.clusterId=kind-kubently');
    expect(args).toContain('executor.apiUrl=http://kubently-api:8080');
    expect(args).toContain('executor.token=tok');
    expect(args).toContain('--wait');
  });
});

describe('secretManifest', () => {
  it('renders an Opaque secret with stringData', () => {
    const y = secretManifest('kubently', 'kubently-api-keys', { keys: 'abc' });
    expect(y).toContain('kind: Secret');
    expect(y).toContain('name: kubently-api-keys');
    expect(y).toContain('namespace: kubently');
    expect(y).toContain('keys: abc');
    expect(y).toContain('type: Opaque');
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kubently-cli/nodejs && npx jest src/lib/installer.test.ts`
Expected: FAIL — `Cannot find module './installer.js'` (or similar resolution error).

- [ ] **Step 3: Implement the helpers**

Create `kubently-cli/nodejs/src/lib/installer.ts`:

```ts
import { spawn, spawnSync, ChildProcess } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import * as yaml from 'js-yaml';

export type Provider = 'anthropic-claude' | 'openai' | 'google-gemini';

const PROVIDER_ENV: Record<Provider, string> = {
  'anthropic-claude': 'ANTHROPIC_API_KEY',
  openai: 'OPENAI_API_KEY',
  'google-gemini': 'GOOGLE_API_KEY',
};

export const HELM_REPO_URL = 'https://kubently.github.io/kubently';

export function providerEnvVar(provider: string): string {
  const envVar = PROVIDER_ENV[provider as Provider];
  if (!envVar) {
    throw new Error(
      `Unknown LLM provider: ${provider} (expected one of: ${Object.keys(PROVIDER_ENV).join(', ')})`
    );
  }
  return envVar;
}

export function genToken(bytes = 32): string {
  return randomBytes(bytes).toString('hex');
}

export function sanitizeClusterId(name: string): string {
  const id = name
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return id || 'default';
}

export interface HelmOpts {
  namespace: string;
  clusterId: string;
  executorToken: string;
  provider: string;
  chartPath?: string; // local chart dir; when absent, install from the published repo
}

export function buildHelmArgs(o: HelmOpts): string[] {
  const chart = o.chartPath ? [o.chartPath] : ['kubently', '--repo', HELM_REPO_URL];
  return [
    'upgrade',
    '--install',
    'kubently',
    ...chart,
    '-n',
    o.namespace,
    '--set',
    'api.existingSecret=kubently-api-keys',
    '--set',
    `api.env.LLM_PROVIDER=${o.provider}`,
    '--set',
    'executor.enabled=true',
    '--set',
    `executor.clusterId=${o.clusterId}`,
    '--set',
    'executor.apiUrl=http://kubently-api:8080',
    '--set',
    `executor.token=${o.executorToken}`,
    '--wait',
    '--timeout',
    '300s',
  ];
}

export function secretManifest(
  namespace: string,
  name: string,
  data: Record<string, string>
): string {
  return yaml.dump({
    apiVersion: 'v1',
    kind: 'Secret',
    metadata: { name, namespace },
    type: 'Opaque',
    stringData: data,
  });
}
```

(The `spawn`/`spawnSync`/`ChildProcess` imports are used by Task 3 — leaving them in now is fine; if `noUnusedLocals` complains, add them in Task 3 instead.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kubently-cli/nodejs && npx jest src/lib/installer.test.ts`
Expected: PASS, all tests green.

- [ ] **Step 5: Commit**

```bash
git add kubently-cli/nodejs/src/lib/installer.ts kubently-cli/nodejs/src/lib/installer.test.ts
git commit -m "feat(cli): pure helpers for kubently install (tokens, helm args, secret manifests)"
```

---

### Task 3: Shell glue in installer.ts

Thin `spawnSync` wrappers — no unit tests (the pure manifest/args builders they consume are already tested; the wrappers get exercised by Task 7's manual kind run).

**Files:**
- Modify: `kubently-cli/nodejs/src/lib/installer.ts` (append)

- [ ] **Step 1: Append the runners**

Append to `kubently-cli/nodejs/src/lib/installer.ts`:

```ts
/** Run a command synchronously; throw with stderr on failure. */
export function run(cmd: string, args: string[], input?: string): string {
  const r = spawnSync(cmd, args, { input, encoding: 'utf-8' });
  if (r.error) throw r.error;
  if (r.status !== 0) {
    throw new Error(`${cmd} ${args.join(' ')} failed:\n${r.stderr}`);
  }
  return r.stdout;
}

/** Verify kubectl and helm exist on PATH. */
export function preflight(): void {
  const checks: Array<[string, string[]]> = [
    ['kubectl', ['version', '--client']],
    ['helm', ['version', '--short']],
  ];
  for (const [tool, args] of checks) {
    const r = spawnSync(tool, args, { encoding: 'utf-8' });
    if (r.error || r.status !== 0) {
      throw new Error(`${tool} not found on PATH — install it first (https://kubently.io/docs)`);
    }
  }
}

export function currentContext(): string {
  return run('kubectl', ['config', 'current-context']).trim();
}

export function ensureNamespace(namespace: string): void {
  const manifest = run('kubectl', [
    'create', 'namespace', namespace, '--dry-run=client', '-o', 'yaml',
  ]);
  run('kubectl', ['apply', '-f', '-'], manifest);
}

/** Idempotent create-or-update via kubectl apply. */
export function applySecret(
  namespace: string,
  name: string,
  data: Record<string, string>
): void {
  run('kubectl', ['apply', '-f', '-'], secretManifest(namespace, name, data));
}

/** Read one key from an existing secret; null when secret or key is absent. */
export function getSecretValue(
  namespace: string,
  name: string,
  key: string
): string | null {
  const r = spawnSync(
    'kubectl',
    ['-n', namespace, 'get', 'secret', name, '-o', `jsonpath={.data.${key}}`],
    { encoding: 'utf-8' }
  );
  if (r.status !== 0 || !r.stdout) return null;
  return Buffer.from(r.stdout, 'base64').toString('utf-8');
}

export function startPortForward(namespace: string, localPort = 8080): ChildProcess {
  const child = spawn(
    'kubectl',
    ['-n', namespace, 'port-forward', 'svc/kubently-api', `${localPort}:8080`],
    { stdio: 'ignore' }
  );
  return child;
}

export function restartExecutor(namespace: string): void {
  run('kubectl', ['-n', namespace, 'rollout', 'restart', 'deploy/kubently-executor']);
  run('kubectl', [
    '-n', namespace, 'rollout', 'status', 'deploy/kubently-executor', '--timeout=120s',
  ]);
}

/** Poll `check` until it returns true or the deadline passes. */
export async function waitFor(
  check: () => Promise<boolean>,
  what: string,
  timeoutMs = 120_000,
  intervalMs = 3_000
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      if (await check()) return;
    } catch {
      // not ready yet — keep polling
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`Timed out waiting for ${what}`);
}
```

- [ ] **Step 2: Verify it compiles and existing tests still pass**

Run: `cd kubently-cli/nodejs && npm run build && npx jest`
Expected: build clean, Task 2 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add kubently-cli/nodejs/src/lib/installer.ts
git commit -m "feat(cli): kubectl/helm shell runners for install flow"
```

---

### Task 4: The `install` command

**Files:**
- Create: `kubently-cli/nodejs/src/commands/install.ts`
- Modify: `kubently-cli/nodejs/src/index.ts` (register command)
- Modify: `kubently-cli/nodejs/package.json` (version 2.1.6 → 2.2.0)

- [ ] **Step 1: Write the command**

Create `kubently-cli/nodejs/src/commands/install.ts`:

```ts
import { Command } from 'commander';
import chalk from 'chalk';
import inquirer from 'inquirer';
import ora from 'ora';
import axios from 'axios';
import { Config } from '../lib/config.js';
import { KubentlyAdminClient } from '../lib/adminClient.js';
import { runDebugSession } from './debug.js';
import {
  buildHelmArgs,
  applySecret,
  currentContext,
  ensureNamespace,
  genToken,
  getSecretValue,
  preflight,
  providerEnvVar,
  restartExecutor,
  run,
  sanitizeClusterId,
  startPortForward,
  waitFor,
} from '../lib/installer.js';

const LOCAL_API_URL = 'http://localhost:8080';

export function installCommand(config: Config): Command {
  const cmd = new Command('install');

  cmd
    .description('🚀 Install Kubently into the current kubectl context and start a debug chat')
    .option('-n, --namespace <ns>', 'Namespace to install into', 'kubently')
    .option('--cluster-id <id>', 'Logical cluster id (default: derived from kube context)')
    .option(
      '--provider <provider>',
      'LLM provider: anthropic-claude | openai | google-gemini',
      'anthropic-claude'
    )
    .option('--llm-api-key <key>', 'LLM API key (default: provider env var, else prompt)')
    .option('--chart <path>', 'Install from a local chart directory instead of the published repo')
    .option('-y, --yes', 'Skip confirmation prompts', false)
    .option('--no-chat', 'Skip starting the interactive chat after install')
    .action(async (opts) => {
      try {
        await runInstall(config, opts);
      } catch (error) {
        console.error(
          chalk.red(`\n✗ Install failed: ${error instanceof Error ? error.message : error}`)
        );
        process.exit(1);
      }
    });

  return cmd;
}

interface InstallOpts {
  namespace: string;
  clusterId?: string;
  provider: string;
  llmApiKey?: string;
  chart?: string;
  yes: boolean;
  chat: boolean;
}

async function runInstall(config: Config, opts: InstallOpts): Promise<void> {
  preflight();
  const ctx = currentContext();
  const namespace = opts.namespace;
  const clusterId = opts.clusterId ?? sanitizeClusterId(ctx);

  console.log(chalk.cyan('\nKubently will be installed with:'));
  console.log(`  kube context : ${chalk.white(ctx)}`);
  console.log(`  namespace    : ${chalk.white(namespace)}`);
  console.log(`  cluster id   : ${chalk.white(clusterId)}`);
  console.log(`  LLM provider : ${chalk.white(opts.provider)}\n`);

  if (!opts.yes) {
    const { proceed } = await inquirer.prompt([
      { type: 'confirm', name: 'proceed', message: 'Continue?', default: true },
    ]);
    if (!proceed) return;
  }

  // LLM key: flag > env var > prompt
  const envVar = providerEnvVar(opts.provider);
  let llmKey = opts.llmApiKey ?? process.env[envVar];
  if (!llmKey) {
    const answer = await inquirer.prompt([
      {
        type: 'password',
        name: 'key',
        mask: '*',
        message: `Enter your ${envVar} (used by the agent to call the LLM):`,
        validate: (input: string) => (input ? true : 'API key is required'),
      },
    ]);
    llmKey = answer.key as string;
  }

  // 1. Namespace + secrets (idempotent; reuse existing api key so config keeps working)
  let spinner = ora('Creating namespace and secrets').start();
  ensureNamespace(namespace);
  const apiKey = getSecretValue(namespace, 'kubently-api-keys', 'keys')?.split('\n')[0] ?? genToken(24);
  applySecret(namespace, 'kubently-api-keys', { keys: apiKey });
  const redisPassword = getSecretValue(namespace, 'kubently-redis-password', 'password') ?? genToken(16);
  applySecret(namespace, 'kubently-redis-password', { password: redisPassword });
  applySecret(namespace, 'kubently-llm-secrets', { [envVar]: llmKey });
  spinner.succeed('Namespace and secrets ready');

  // 2. Helm install (waits for deployments)
  const executorToken = genToken(32);
  spinner = ora('Installing Kubently via Helm (this can take a few minutes)').start();
  run('helm', buildHelmArgs({
    namespace,
    clusterId,
    executorToken,
    provider: opts.provider,
    chartPath: opts.chart,
  }));
  spinner.succeed('Helm release installed');

  // 3. Port-forward + wait for API
  spinner = ora('Waiting for the Kubently API').start();
  const portForward = startPortForward(namespace);
  await waitFor(
    async () => (await axios.get(`${LOCAL_API_URL}/healthz`, { timeout: 2000 })).status === 200,
    'API health check'
  );
  spinner.succeed('API is up (port-forwarded to localhost:8080)');

  // 4. Register the executor token (the glue Helm doesn't do), bounce executor
  spinner = ora('Registering executor with the API').start();
  const admin = new KubentlyAdminClient(LOCAL_API_URL, apiKey);
  await admin.createAgentToken(clusterId, executorToken);
  restartExecutor(namespace);
  await waitFor(async () => {
    const { clusters } = await admin.listClusters();
    return clusters.some((c) => c.id === clusterId && c.connected);
  }, `cluster '${clusterId}' to connect`);
  spinner.succeed(`Cluster '${clusterId}' connected`);

  // 5. Persist CLI config
  config.setApiUrl(LOCAL_API_URL);
  config.setApiKey(apiKey);
  config.save();
  console.log(chalk.green('\n✓ Kubently installed. CLI config saved to ~/.kubently/config.json'));
  console.log(chalk.gray(`  Port-forward is running in the background (pid ${portForward.pid}).`));
  console.log(chalk.gray('  Re-create it later with:'));
  console.log(chalk.gray(`  kubectl -n ${namespace} port-forward svc/kubently-api 8080:8080\n`));

  // 6. Straight into the chat
  if (opts.chat) {
    await runDebugSession(LOCAL_API_URL, apiKey, clusterId);
  }
  portForward.kill();
}
```

- [ ] **Step 2: Register in index.ts and bump version**

In `kubently-cli/nodejs/src/index.ts`, add the import next to the other command imports:

```ts
import { installCommand } from './commands/install.js';
```

and register it right before `program.addCommand(initCommand(config));`:

```ts
program.addCommand(installCommand(config));
```

In `kubently-cli/nodejs/package.json`, change `"version": "2.1.6"` to `"version": "2.2.0"`.

- [ ] **Step 3: Verify build + help output**

Run: `cd kubently-cli/nodejs && npm run build && node dist/index.js install --help`
Expected: help text listing `--namespace`, `--cluster-id`, `--provider`, `--llm-api-key`, `--chart`, `--yes`, `--no-chat`.

Run: `npx jest`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add kubently-cli/nodejs/src/commands/install.ts kubently-cli/nodejs/src/index.ts kubently-cli/nodejs/package.json
git commit -m "feat(cli): kubently install — one-command cluster onboarding into debug chat"
```

---

### Task 5: Publish the Helm chart (gh-pages via chart-releaser)

**Files:**
- Create: `.github/workflows/release-chart.yml`

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/release-chart.yml`:

```yaml
name: Release Helm Chart

on:
  push:
    branches: [main]
    paths:
      - 'deployment/helm/kubently/**'

jobs:
  release:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Configure Git
        run: |
          git config user.name "$GITHUB_ACTOR"
          git config user.email "$GITHUB_ACTOR@users.noreply.github.com"

      - name: Run chart-releaser
        uses: helm/chart-releaser-action@v1.7.0
        with:
          charts_dir: deployment/helm
        env:
          CR_TOKEN: '${{ secrets.GITHUB_TOKEN }}'
```

- [ ] **Step 2: Create the gh-pages branch (one-time, required by chart-releaser)**

```bash
git checkout --orphan gh-pages
git rm -rf . && git commit --allow-empty -m "chore: init gh-pages for helm repo"
git push origin gh-pages
git checkout -
```

(If the push is rejected or Pages needs enabling, note it for the user: GitHub → Settings → Pages → deploy from `gh-pages` branch.)

- [ ] **Step 3: Commit the workflow**

```bash
git add .github/workflows/release-chart.yml
git commit -m "ci: publish helm chart to gh-pages via chart-releaser"
```

Verification happens after merge to main: the workflow run should create a `kubently-1.0.0` GitHub release and `https://kubently.github.io/kubently/index.yaml` should resolve. Until then, `kubently install --chart ./deployment/helm/kubently` is the working path.

---

### Task 6: README quickstart + changelog

**Files:**
- Modify: `README.md` (add/replace quickstart section near the top)
- Modify: `CHANGELOG.md` (new entry)

- [ ] **Step 1: Add the quickstart section**

Read `README.md` first and place this section immediately after the project intro, replacing any existing multi-step quickstart (keep the old manual path further down under "Manual installation" if one exists):

```markdown
## Quickstart (5 minutes)

Point `kubectl` at any cluster (kind, minikube, or real) and run:

​```bash
npm install -g @kubently/cli
kubently install
​```

That's it. The CLI installs Kubently via Helm, wires up secrets and the
executor, port-forwards the API, and drops you into a debug chat:

​```
kubently> why is my nginx pod crashlooping?
​```

You'll need an LLM API key (Anthropic, OpenAI, or Google) — the installer
prompts for it, or reads `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
`GOOGLE_API_KEY` from your environment. Use `--provider` to pick the LLM,
`--chart ./deployment/helm/kubently` to install from a local checkout, and
`kubently install --help` for everything else.
```

(Remove the `​` zero-width characters — they only escape the nested code fences in this plan.)

- [ ] **Step 2: Changelog entry**

Add to the top of `CHANGELOG.md` following its existing format:

```markdown
## 2026-07-06

### Added
- `kubently install`: one-command onboarding — installs Kubently via Helm into the current kubectl context, creates all secrets, registers the executor, and starts a debug chat (CLI 2.2.0).
- Helm chart published to https://kubently.github.io/kubently via chart-releaser GitHub Action.
```

- [ ] **Step 3: Commit**

```bash
git add README.md CHANGELOG.md
git commit -m "docs: quickstart with kubently install; changelog"
```

Demo GIF is a deliberate follow-up (needs a human-quality recording; consider `vhs` for a scripted terminal recording) — not part of this plan.

---

### Task 7: Manual E2E verification on kind (exit criterion)

No files — this validates the spec's exit criterion: fresh cluster → answered diagnosis in under 5 minutes, zero YAML edits.

- [ ] **Step 1: Fresh cluster + timed install**

```bash
kind delete cluster --name kubently-quickstart 2>/dev/null || true
kind create cluster --name kubently-quickstart
cd kubently-cli/nodejs && npm run build
time node dist/index.js install \
  --chart ../../deployment/helm/kubently \
  --yes --no-chat
```

Expected: exits 0; `✓ Kubently installed`; cluster id `kind-kubently-quickstart` connected. Note the `time` output — target is under 5 minutes (image pulls dominate).

- [ ] **Step 2: Verify the chat answers a diagnosis**

```bash
node dist/index.js debug
```

At the `kubently>` prompt ask: `what pods are running in the kube-system namespace?`
Expected: streamed agent answer listing kube-system pods (coredns, kindnet, etc.). Type `exit`.

- [ ] **Step 3: Verify idempotent rerun**

Run the same install command again.
Expected: exits 0, reuses the existing API key (`~/.kubently/config.json` unchanged apart from timestamps), cluster reconnects.

- [ ] **Step 4: Clean up and record results**

```bash
kind delete cluster --name kubently-quickstart
```

Record the timed result in the PR description. If over 5 minutes, note where the time went (almost certainly image pull) — pre-pulled images on a real cluster will be faster; do not micro-optimize yet.

---

## Self-Review Notes

- **Spec coverage:** one-command install (Tasks 2–4), published Helm repo (Task 5), versioned release path (CLI version bump Task 4 + existing `publish-npm.yml`; chart releases via Task 5), README quickstart (Task 6), 5-minute exit criterion (Task 7). Demo GIF explicitly deferred (needs human recording).
- **Known risk:** `helm upgrade --install --wait` can hit the `.spec.replicas` field-manager conflict documented in `kind-e2e.sh`; if Task 7 hits it, the fix (delete the `kubently-api` deployment and rerun) is acceptable for a rerun path — do not preemptively code around it.
- **Port-forward lifetime:** the child dies with the CLI process; the success message prints the manual re-create command. Good enough for v1.
