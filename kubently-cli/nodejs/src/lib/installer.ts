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
    'api.enabled=true',
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
  return spawn(
    'kubectl',
    ['-n', namespace, 'port-forward', 'svc/kubently-api', `${localPort}:8080`],
    { stdio: 'ignore' }
  );
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
