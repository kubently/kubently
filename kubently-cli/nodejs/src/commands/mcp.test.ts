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
