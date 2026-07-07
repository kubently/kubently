import { describe, it, expect } from '@jest/globals';
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
    // api.enabled has no default in values.yaml — omitting it renders no API deployment
    expect(args).toContain('api.enabled=true');
    expect(args).toContain('api.existingSecret=kubently-api-keys');
    expect(args).toContain('api.env.LLM_PROVIDER=anthropic-claude');
    expect(args).toContain('executor.enabled=true');
    expect(args).toContain('executor.clusterId=kind-kubently');
    expect(args).toContain('executor.apiUrl=http://kubently-api:8080');
    expect(args).toContain('executor.token=tok');
    expect(args).toContain('--wait');
  });

  it('pins a current Anthropic model (published image default is retired)', () => {
    expect(buildHelmArgs(base)).toContain('api.env.ANTHROPIC_MODEL_NAME=claude-sonnet-4-6');
    expect(buildHelmArgs({ ...base, provider: 'openai' }).join(' ')).not.toContain(
      'ANTHROPIC_MODEL_NAME'
    );
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
