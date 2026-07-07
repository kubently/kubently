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
