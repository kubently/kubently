/**
 * Commander wiring contract.
 *
 * Every user-facing command is a `Command` built by a factory in this
 * directory, and nothing verified that those factories still produce a
 * well-formed command tree. Commander majors have repeatedly changed option
 * parsing, `opts()` key casing and argument handling — all of which type-check
 * fine and only surface when a user runs the CLI.
 *
 * Contracts under guard:
 * - Each factory returns a Command with the expected name.
 * - The flags users type still register, and still map to the camelCase keys
 *   the action handlers read off `opts()`.
 * - Parsing a realistic argv produces the expected option values.
 */

import { describe, it, expect, jest } from '@jest/globals';
import { Command } from 'commander';

// `open` is ESM-only and computes __dirname from import.meta.url, which cannot
// be transpiled to CJS. It only launches a browser during the device-code login
// flow, so stubbing it keeps the whole command tree importable here.
jest.mock('open', () => ({ __esModule: true, default: jest.fn() }));

import { mcpCommand } from './mcp.js';
import { initCommand } from './init.js';
import { installCommand } from './install.js';
import { clusterCommands } from './cluster.js';
import { debugCommand } from './debug.js';
import { execCommand } from './exec.js';
import { createLoginCommand } from './login.js';
import { Config } from '../lib/config.js';

const config = new Config();

/** The long flags a command registers, e.g. ['--api-url', '--api-key']. */
function flagsOf(cmd: Command): string[] {
  return cmd.options.map((o) => o.long).filter((f): f is string => Boolean(f));
}

describe('command factories', () => {
  const factories: Array<[string, () => Command]> = [
    ['mcp', () => mcpCommand(config)],
    ['init', () => initCommand(config)],
    ['install', () => installCommand(config)],
    ['cluster', () => clusterCommands(config)],
    ['debug', () => debugCommand(config)],
    ['exec', () => execCommand(config)],
    ['login', () => createLoginCommand()],
  ];

  it.each(factories)('%s builds a Command with a description', (name, build) => {
    const cmd = build();
    expect(cmd).toBeInstanceOf(Command);
    expect(cmd.name()).toBe(name);
    expect(cmd.description()).toBeTruthy();
  });
});

describe('option registration', () => {
  it('mcp exposes the api-url/api-key overrides its action reads', () => {
    expect(flagsOf(mcpCommand(config)).sort()).toEqual(['--api-key', '--api-url']);
  });

  it('parses --api-url/--api-key into the camelCase keys the handler uses', () => {
    // mcpCommand's action reads opts.apiUrl / opts.apiKey. Commander derives
    // those names from the flags; a change in that derivation silently yields
    // undefined and the command exits 1 with "missing API URL or key".
    const cmd = mcpCommand(config);
    cmd.action(() => {}); // replace the spawning action
    cmd.parse(['--api-url', 'http://localhost:8080', '--api-key', 'k3y'], { from: 'user' });

    const opts = cmd.opts();
    expect(opts.apiUrl).toBe('http://localhost:8080');
    expect(opts.apiKey).toBe('k3y');
  });
});

describe('root program wiring', () => {
  /** Mirrors the global options index.ts registers on the root program. */
  function rootProgram(): Command {
    return new Command()
      .name('kubently')
      .description('Kubently - Troubleshooting Kubernetes Agentically')
      .option('--api-url <url>', 'Kubently API URL (overrides config/env)')
      .option('--api-key <key>', 'API key for authentication (overrides config/env)')
      .option('--a2a-path <path>', 'Custom A2A endpoint path (default: /a2a)')
      .option('--debug', 'Enable debug output', false);
  }

  it('registers subcommands under the root program', () => {
    const program = rootProgram();
    program.addCommand(mcpCommand(config));
    program.addCommand(initCommand(config));
    program.addCommand(clusterCommands(config));

    expect(program.commands.map((c) => c.name()).sort()).toEqual(['cluster', 'init', 'mcp']);
  });

  it('maps --a2a-path to opts.a2aPath and defaults --debug to false', () => {
    // `--a2a-path` -> `a2aPath` is the fiddliest of commander's camelCase rules
    // (digit boundary), and index.ts's preAction hook depends on it exactly.
    const program = rootProgram();
    program.parse(['--a2a-path', '/custom-a2a'], { from: 'user' });

    expect(program.opts().a2aPath).toBe('/custom-a2a');
    expect(program.opts().debug).toBe(false);
  });

  it('honours a preAction hook, which index.ts uses to apply global options', () => {
    const seen: string[] = [];
    const program = rootProgram()
      .hook('preAction', (thisCommand) => {
        seen.push(String(thisCommand.opts().apiUrl));
      })
      .action(() => {});

    program.parse(['--api-url', 'https://kubently.example.com'], { from: 'user' });
    expect(seen).toEqual(['https://kubently.example.com']);
  });
});
