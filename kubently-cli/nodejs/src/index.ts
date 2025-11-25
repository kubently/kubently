#!/usr/bin/env node

import { Command } from 'commander';
import chalk from 'chalk';
import figlet from 'figlet';
import { initCommand } from './commands/init.js';
import { clusterCommands } from './commands/cluster.js';
import { debugCommand } from './commands/debug.js';
import { createLoginCommand } from './commands/login.js';
import { Config } from './lib/config.js';
import { runInteractiveMode } from './commands/interactive.js';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const packageJson = JSON.parse(readFileSync(join(__dirname, '../package.json'), 'utf-8'));
const version = packageJson.version;

const program = new Command();
const config = new Config();

// ASCII Art Banner
function showBanner() {
  console.log(
    chalk.cyan(
      figlet.textSync('Kubently', {
        horizontalLayout: 'default',
        verticalLayout: 'default'
      })
    )
  );
  console.log(chalk.green(`  🚀 Troubleshooting Kubernetes Agentically v${version}\n`));
}

// Main CLI configuration
program
  .name('kubently')
  .description('Kubently - Troubleshooting Kubernetes Agentically')
  .version(version, '-v, --version', 'Display version number')
  .option('--api-url <url>', 'Kubently API URL (overrides config/env)')
  .option('--api-key <key>', 'API key for authentication (overrides config/env)')
  .option('--a2a-path <path>', 'Custom A2A endpoint path (default: /a2a)')
  .option('--debug', 'Enable debug output', false)
  .hook('preAction', (thisCommand) => {
    // Show banner for main commands (not subcommands)
    if (thisCommand.name() === 'kubently' && process.argv.length > 2) {
      showBanner();
    }
    
    // Apply global options to config (but skip for login command)
    // The login command handles its own api-key option
    const opts = thisCommand.opts();
    const isLoginCommand = process.argv[2] === 'login';
    
    if (opts.apiUrl) {
      config.setApiUrl(opts.apiUrl);
    }
    // Don't apply global api-key for login command
    if (opts.apiKey && !isLoginCommand) {
      config.setApiKey(opts.apiKey);
    }
    if (opts.a2aPath) {
      config.setA2aPath(opts.a2aPath);
    }
    if (opts.debug) {
      process.env.DEBUG = 'true';
    }
  });

// Add commands
program.addCommand(initCommand(config));
program.addCommand(createLoginCommand());
program.addCommand(clusterCommands(config));
program.addCommand(debugCommand(config));

// Version command with extra info
program
  .command('version')
  .description('Show detailed version information')
  .action(() => {
    console.log(chalk.cyan('╔═══════════════════════════════════╗'));
    console.log(chalk.cyan('║') + chalk.white('     Kubently CLI Information      ') + chalk.cyan('║'));
    console.log(chalk.cyan('╠═══════════════════════════════════╣'));
    console.log(chalk.cyan('║') + ' CLI Version:     ' + chalk.green(`v${version}`.padEnd(17)) + chalk.cyan('║'));
    console.log(chalk.cyan('║') + ' A2A Protocol:    ' + chalk.green('v1.0'.padEnd(17)) + chalk.cyan('║'));
    console.log(chalk.cyan('║') + ' Node.js:         ' + chalk.green(process.version.padEnd(17)) + chalk.cyan('║'));
    console.log(chalk.cyan('║') + ' Platform:        ' + chalk.green(process.platform.padEnd(17)) + chalk.cyan('║'));
    console.log(chalk.cyan('╚═══════════════════════════════════╝'));
  });

// Add a default action for when no command is specified
program.action(async () => {
  const opts = program.opts();
  
  // If api-url and api-key are provided, run interactive mode
  if (opts.apiUrl && opts.apiKey) {
    await runInteractiveMode(config);
  } else {
    // Show help if no valid options
    program.help();
  }
});


// Add exit handlers for debugging
process.on('exit', (code) => {
  if (process.env.DEBUG === 'true') {
    console.log(`\nDEBUG: Process exiting with code ${code}`);
    console.trace('Exit stack trace');
  }
});

process.on('beforeExit', (code) => {
  if (process.env.DEBUG === 'true') {
    console.log(`\nDEBUG: Process about to exit with code ${code}`);
  }
});

// Parse arguments
program.parse(process.argv);

// Show help if no arguments
if (process.argv.length === 2) {
  showBanner();
  program.help();
}