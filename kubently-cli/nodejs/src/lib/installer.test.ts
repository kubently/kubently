import { describe, it, expect } from '@jest/globals';
import * as yaml from 'js-yaml';
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

/**
 * js-yaml contract.
 *
 * `secretManifest` output is piped straight into `kubectl apply -f -`, so the
 * dumped YAML is the only thing standing between a generated token and the
 * cluster. The assertions above match on rendered substrings, which silently
 * pass or fail on cosmetic quoting changes; these parse the document back and
 * assert on values, which is what a js-yaml major bump can actually break.
 */
describe('secretManifest / js-yaml serialisation', () => {
  const load = (y: string) => yaml.load(y) as any;

  it('round-trips to the exact Secret object kubectl expects', () => {
    const doc = load(secretManifest('kubently', 'kubently-api-keys', { keys: 'abc' }));
    expect(doc).toEqual({
      apiVersion: 'v1',
      kind: 'Secret',
      metadata: { name: 'kubently-api-keys', namespace: 'kubently' },
      type: 'Opaque',
      stringData: { keys: 'abc' },
    });
  });

  it('preserves generated tokens byte-for-byte', () => {
    const token = genToken(32);
    const doc = load(secretManifest('kubently', 'kubently-executor-token', { token }));
    expect(doc.stringData.token).toBe(token);
  });

  it('quotes values that would otherwise parse as non-strings', () => {
    // A key that looks like a number, a bool, or null must survive as a string —
    // an unquoted `123456` becomes an int and kubectl rejects the Secret.
    const doc = load(
      secretManifest('kubently', 's', {
        numeric: '123456',
        boolish: 'true',
        nullish: 'null',
        version: '1.10',
      })
    );
    expect(doc.stringData.numeric).toBe('123456');
    expect(doc.stringData.boolish).toBe('true');
    expect(doc.stringData.nullish).toBe('null');
    expect(doc.stringData.version).toBe('1.10');
  });

  it('survives YAML metacharacters and multi-line keys without injection', () => {
    // Multi-line is the real case: the api-keys secret holds newline-separated keys.
    const multi = 'key-one\nkey-two\nkey-three';
    const nasty = 'a: b #comment\n- item\n"quoted"\t\\backslash';
    const doc = load(secretManifest('kubently', 's', { keys: multi, weird: nasty }));

    expect(doc.stringData.keys).toBe(multi);
    expect(doc.stringData.weird).toBe(nasty);
    // The injected text must stay inside stringData, not become new top-level keys.
    expect(Object.keys(doc).sort()).toEqual(['apiVersion', 'kind', 'metadata', 'stringData', 'type']);
  });

  it('emits a single document with no anchors or aliases', () => {
    // Repeating the same value must not emit `&a`/`*a` references — kubectl
    // handles them, but they make the manifest unreadable in review and have
    // historically shifted between js-yaml majors.
    const shared = 'same-value';
    const y = secretManifest('kubently', 's', { a: shared, b: shared });
    expect(y).not.toMatch(/[&*]ref/);
    expect(y.split('---').length).toBe(1);
    expect(load(y).stringData).toEqual({ a: shared, b: shared });
  });
});
