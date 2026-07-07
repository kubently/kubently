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
