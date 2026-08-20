/**
 * `kubently audit` export contract.
 *
 * The point of the subcommand is that its output can be handed to something
 * else -- a spreadsheet, a compliance reviewer, `jq`. So the two things worth
 * guarding are the ones that break silently: the CSV must survive kubectl
 * arguments containing commas and quotes, and the JSON must round-trip.
 */

import { describe, it, expect, jest, beforeEach, afterEach } from '@jest/globals';
import { toCsv, parseSince } from './audit.js';
import { AuditEntry } from '../lib/adminClient.js';

function entry(overrides: Partial<AuditEntry> = {}): AuditEntry {
  return {
    timestamp: '2026-08-20T10:00:00+00:00',
    type: 'command_executed',
    service_identity: 'team-a',
    cluster_id: 'prod-a',
    session_id: 'sess-1',
    command_id: 'cmd-1',
    command: 'get pods -n kube-system',
    outcome: 'success',
    error: null,
    correlation_id: null,
    ...overrides,
  };
}

describe('CSV export', () => {
  it('emits a header row followed by one row per entry', () => {
    const lines = toCsv([entry(), entry({ command_id: 'cmd-2' })]).split('\n');

    expect(lines).toHaveLength(3);
    expect(lines[0]).toBe(
      'timestamp,type,cluster_id,session_id,command_id,command,outcome,error,correlation_id'
    );
  });

  it('survives commas and quotes in a command', () => {
    // `-l app=web,tier=api` is an ordinary selector, and an unquoted comma
    // would shift every column after it by one.
    const csv = toCsv([
      entry({ command: 'get pods -l app=web,tier=api -o jsonpath="{.items[0]}"' }),
    ]);
    const row = csv.split('\n')[1];

    expect(row).toContain('"get pods -l app=web,tier=api -o jsonpath=""{.items[0]}"""');
    // One field per column: quoting keeps the embedded comma inside its field.
    expect(row.match(/","/g)).toHaveLength(8);
  });

  it('renders nulls as empty fields rather than the string "null"', () => {
    const row = toCsv([entry({ error: null, session_id: null })]).split('\n')[1];
    expect(row).not.toContain('null');
    expect(row).toContain('""');
  });

  it('emits a usable header even with no entries', () => {
    expect(toCsv([])).toBe(
      'timestamp,type,cluster_id,session_id,command_id,command,outcome,error,correlation_id'
    );
  });
});

describe('JSON export', () => {
  it('round-trips', () => {
    const entries = [entry(), entry({ outcome: 'failure', error: 'Forbidden' })];
    expect(JSON.parse(JSON.stringify(entries))).toEqual(entries);
  });
});

describe('--since parsing', () => {
  // The clock is frozen rather than sampled. These assertions are about
  // arithmetic, not timing, and racing a live `Date.now()` made them flaky:
  // wall-clock time can step backwards on a CI runner under NTP, which is how
  // `ago('30s')` came back as 29999 and failed `>= 30000`.
  const NOW = Date.parse('2026-08-20T12:00:00.000Z');

  beforeEach(() => {
    jest.useFakeTimers().setSystemTime(NOW);
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('accepts relative offsets', () => {
    expect(parseSince('2h')).toBe('2026-08-20T10:00:00.000Z');
  });

  it('accepts every supported unit', () => {
    const ago = (spec: string) => NOW - Date.parse(parseSince(spec));

    expect(ago('30s')).toBe(30 * 1000);
    expect(ago('30m')).toBe(30 * 60 * 1000);
    expect(ago('2h')).toBe(2 * 3600 * 1000);
    expect(ago('7d')).toBe(7 * 86400 * 1000);
  });

  it('passes absolute times through as ISO 8601', () => {
    expect(parseSince('2026-08-20T10:00:00Z')).toBe('2026-08-20T10:00:00.000Z');
  });

  it('rejects something that is not a time', () => {
    expect(() => parseSince('last tuesday')).toThrow(/Invalid time/);
  });
});
