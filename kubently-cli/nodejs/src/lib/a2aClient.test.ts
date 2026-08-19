/**
 * Contract tests for the A2A request the CLI puts on the wire.
 *
 * These exist to guard a dependency bump, not agent behaviour. `sendMessage`
 * is the only consumer of `uuid` in the CLI, and the A2A protocol is strict
 * about the request envelope (see docs/TEST_QUERIES.md) — a malformed
 * messageId or a dropped contextId is accepted by the type checker and fails
 * only against a live server, which nothing in CI exercises.
 *
 * Contracts under guard:
 * - uuid still produces RFC-4122 v4 strings for messageId/partId/contextId.
 * - Every message in a session shares one contextId but gets a fresh
 *   messageId, which is what makes multi-turn memory work server-side.
 * - The JSON-RPC envelope keeps the exact shape the A2A server expects.
 * - clusterId travels in `params.metadata`, and is omitted (not null) when unset.
 */

import { describe, it, expect, jest, beforeEach } from '@jest/globals';

const post = jest.fn<any>();

jest.mock('axios', () => ({
  __esModule: true,
  default: {
    create: jest.fn(() => ({
      post,
      interceptors: { request: { use: jest.fn() } },
    })),
    isAxiosError: jest.fn(() => false),
  },
}));

// jest.mock is hoisted above this import, so the module gets the stubbed axios.
import { KubentlyA2ASession } from './a2aClient.js';

/** RFC-4122 v4: version nibble is 4, variant nibble is 8/9/a/b. */
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/** A minimal SSE body the way the A2A server streams it. */
function sseResponse(text: string) {
  return {
    data: [
      `data: ${JSON.stringify({ result: { artifact: { parts: [{ text }] } } })}`,
      'data: [DONE]',
      '',
    ].join('\n'),
  };
}

describe('KubentlyA2ASession request envelope', () => {
  beforeEach(() => {
    post.mockReset();
    post.mockResolvedValue(sseResponse('ok'));
  });

  it('generates RFC-4122 v4 ids for messageId, partId and contextId', async () => {
    const session = new KubentlyA2ASession('http://localhost:8080/a2a/', 'k3y');
    await session.sendMessage('show crashing pods');

    const [, body] = post.mock.calls[0] as [string, any];
    expect(body.params.message.messageId).toMatch(UUID_V4);
    expect(body.params.message.parts[0].partId).toMatch(UUID_V4);
    expect(body.params.message.contextId).toMatch(UUID_V4);
  });

  it('keeps one contextId per session but a fresh messageId per turn', async () => {
    const session = new KubentlyA2ASession('http://localhost:8080/a2a/', 'k3y');
    await session.sendMessage('first');
    await session.sendMessage('second');

    const [, first] = post.mock.calls[0] as [string, any];
    const [, second] = post.mock.calls[1] as [string, any];

    // Same conversation: the server threads memory on contextId.
    expect(second.params.message.contextId).toBe(first.params.message.contextId);
    // Distinct turns: a repeated messageId is a protocol violation.
    expect(second.params.message.messageId).not.toBe(first.params.message.messageId);
    // JSON-RPC ids increment per request.
    expect(first.id).toBe('1');
    expect(second.id).toBe('2');
  });

  it('builds the JSON-RPC envelope the A2A server expects', async () => {
    const session = new KubentlyA2ASession('http://localhost:8080/a2a/', 'k3y');
    await session.sendMessage('show crashing pods');

    const [, body] = post.mock.calls[0] as [string, any];
    expect(body.jsonrpc).toBe('2.0');
    expect(body.method).toBe('message/stream');
    expect(body.params.message.role).toBe('user');
    expect(body.params.message.parts).toHaveLength(1);
    expect(body.params.message.parts[0].text).toBe('show crashing pods');
  });

  it('passes clusterId through metadata, and omits metadata when unset', async () => {
    const scoped = new KubentlyA2ASession('http://localhost:8080/a2a/', 'k3y', 'prod-cluster');
    await scoped.sendMessage('show crashing pods');
    const [, scopedBody] = post.mock.calls[0] as [string, any];
    expect(scopedBody.params.metadata).toEqual({ clusterId: 'prod-cluster' });

    post.mockClear();
    const unscoped = new KubentlyA2ASession('http://localhost:8080/a2a/', 'k3y');
    await unscoped.sendMessage('show crashing pods');
    const [, unscopedBody] = post.mock.calls[0] as [string, any];
    expect(unscopedBody.params.metadata).toBeUndefined();
  });

  it('returns the streamed text and reports server errors', async () => {
    const session = new KubentlyA2ASession('http://localhost:8080/a2a/', 'k3y');

    post.mockResolvedValueOnce(sseResponse('nginx-0 is CrashLoopBackOff'));
    await expect(session.sendMessage('why')).resolves.toMatchObject({
      success: true,
      output: 'nginx-0 is CrashLoopBackOff',
    });

    post.mockResolvedValueOnce({
      data: `data: ${JSON.stringify({ error: { code: -32000, message: 'cluster unreachable' } })}\n`,
    });
    await expect(session.sendMessage('why')).resolves.toMatchObject({
      success: false,
      error: 'cluster unreachable',
    });
  });
});
