import { Command } from 'commander';
import chalk from 'chalk';
import ora from 'ora';
import { Config } from '../lib/config.js';
import { KubentlyAdminClient, AuditEntry } from '../lib/adminClient.js';

/**
 * `kubently audit` - read back the command audit trail.
 *
 * The API decides what this caller may see; the CLI only asks and formats.
 * There is deliberately no flag here for reading somebody else's scope,
 * because there is no such request to make.
 */

/** ISO 8601 for an absolute time, or a relative offset like `2h` / `7d`. */
export function parseSince(value: string): string {
  const relative = /^(\d+)([smhd])$/.exec(value.trim());
  if (relative) {
    const amount = Number(relative[1]);
    const seconds = { s: 1, m: 60, h: 3600, d: 86400 }[relative[2]]!;
    return new Date(Date.now() - amount * seconds * 1000).toISOString();
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    throw new Error(`Invalid time '${value}'. Use ISO 8601 (2026-08-20T10:00:00Z) or an offset (30m, 2h, 7d).`);
  }
  return parsed.toISOString();
}

/** The columns an export carries, in order. */
const COLUMNS: Array<keyof AuditEntry> = [
  'timestamp',
  'type',
  'cluster_id',
  'session_id',
  'command_id',
  'command',
  'outcome',
  'error',
  'correlation_id',
];

/**
 * RFC 4180: quote every field, double the embedded quotes. Quoting
 * unconditionally is shorter than deciding when to, and kubectl arguments are
 * full of commas and spaces that would otherwise need the decision.
 */
export function toCsv(entries: AuditEntry[]): string {
  const escape = (value: unknown): string => `"${String(value ?? '').replace(/"/g, '""')}"`;
  const rows = entries.map((entry) => COLUMNS.map((column) => escape(entry[column])).join(','));
  return [COLUMNS.join(','), ...rows].join('\n');
}

function renderTable(entries: AuditEntry[]): void {
  console.log(
    chalk.gray('TIME'.padEnd(22)) +
      chalk.gray('CLUSTER'.padEnd(18)) +
      chalk.gray('OUTCOME'.padEnd(10)) +
      chalk.gray('COMMAND')
  );

  for (const entry of entries) {
    // Second precision: an audit list is scanned by eye, and milliseconds
    // push the command column off an 80-column terminal.
    const time = (entry.timestamp || '').replace('T', ' ').slice(0, 19);
    const outcome = entry.outcome || '-';
    const colour =
      outcome === 'success' ? chalk.green : outcome === 'timeout' ? chalk.yellow : outcome === '-' ? chalk.gray : chalk.red;

    console.log(
      chalk.white(time.padEnd(22)) +
        chalk.cyan((entry.cluster_id || '-').padEnd(18)) +
        colour(outcome.padEnd(10)) +
        chalk.white(entry.command || entry.type)
    );
  }
}

export function auditCommand(config: Config): Command {
  const audit = new Command('audit');

  audit
    .description('📜 Show the command audit trail (read-only)')
    .option('-c, --cluster <cluster-id>', 'Only commands run against this cluster')
    .option('-s, --session <session-id>', 'Only commands from this debug session')
    .option('--since <time>', 'Only entries after this time (ISO 8601, or 30m/2h/7d)')
    .option('--until <time>', 'Only entries before this time (ISO 8601, or 30m/2h/7d)')
    .option('-n, --limit <count>', 'Maximum entries to show', '100')
    .option('--all', 'Include authentication events, not just executed commands', false)
    .option('-o, --output <format>', 'Output format: table, json, or csv', 'table')
    .action(async (options) => {
      const apiUrl = config.getApiUrl();
      const apiKey = config.getApiKey();

      if (!apiUrl || !apiKey) {
        console.log(chalk.red('✗ API URL and API key are required.'));
        console.log(chalk.yellow('Run "kubently init" or set environment variables:'));
        console.log(chalk.gray('  export KUBENTLY_API_URL=http://your-api-url'));
        console.log(chalk.gray('  export KUBENTLY_API_KEY=your-api-key'));
        process.exit(1);
      }

      const format = String(options.output).toLowerCase();
      if (!['table', 'json', 'csv'].includes(format)) {
        console.log(chalk.red(`✗ Unknown output format '${options.output}'. Use table, json, or csv.`));
        process.exit(1);
      }

      // Machine-readable output owns stdout: a spinner or a banner in the
      // middle of a JSON document makes the export unparseable.
      const quiet = format !== 'table';
      const spinner = quiet ? null : ora('Fetching audit trail...').start();

      try {
        const client = new KubentlyAdminClient(apiUrl, apiKey);
        const result = await client.getAudit({
          // The trail shares one Redis list with an `api_key_verified` event
          // per authenticated request, which outnumbers the commands several
          // to one. Default to the commands; --all shows the rest.
          event_type: options.all ? undefined : 'command_executed',
          cluster_id: options.cluster,
          session_id: options.session,
          since: options.since ? parseSince(options.since) : undefined,
          until: options.until ? parseSince(options.until) : undefined,
          limit: Number(options.limit),
        });

        spinner?.stop();

        if (format === 'json') {
          console.log(JSON.stringify(result.entries, null, 2));
          return;
        }
        if (format === 'csv') {
          console.log(toCsv(result.entries));
          return;
        }

        if (result.entries.length === 0) {
          console.log(chalk.yellow('No audit entries found for this API key.'));
          console.log(
            chalk.gray(`The trail is scoped to '${result.service_identity}' — it shows the commands this key ran.`)
          );
          if (!options.all) {
            console.log(chalk.gray('Pass --all to include authentication events.'));
          }
          return;
        }

        console.log(chalk.cyan(`\nAudit trail for ${chalk.bold(result.service_identity)} — ${result.count} entr${result.count === 1 ? 'y' : 'ies'}\n`));
        renderTable(result.entries);
        console.log('');
      } catch (error) {
        spinner?.fail('Failed to fetch audit trail');
        console.log(chalk.red(`✗ Error: ${error instanceof Error ? error.message : 'Unknown error'}`));
        process.exit(1);
      }
    });

  return audit;
}
