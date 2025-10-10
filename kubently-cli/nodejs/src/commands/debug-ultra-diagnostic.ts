import { Command } from 'commander';
import chalk from 'chalk';
import * as readline from 'readline';
import ora from 'ora';
import { Config } from '../lib/config.js';
import { KubentlyA2ASession } from '../lib/a2aClient.js';

let logId = 0;
function log(msg: string, data?: any) {
  const timestamp = new Date().toISOString();
  console.error(chalk.yellow(`[DIAG ${++logId}] ${timestamp} ${msg}`));
  if (data) console.error(chalk.gray(JSON.stringify(data, null, 2)));
}

function printWelcome() {
  const boxWidth = 61;
  const innerWidth = boxWidth - 4;
  
  console.log(chalk.cyan('\n╔═══════════════════════════════════════════════════════════╗'));
  console.log(chalk.cyan('║') + chalk.white('           🚀 Kubently Debug Session'.padEnd(innerWidth + 3)) + chalk.cyan('║'));
  console.log(chalk.cyan('╠═══════════════════════════════════════════════════════════╣'));
  console.log(chalk.cyan('║ ') + chalk.white('Mode:       ') + chalk.green('Agent Chat (A2A Protocol)'.padEnd(innerWidth - 12)) + chalk.cyan('║'));
  console.log(chalk.cyan('╠═══════════════════════════════════════════════════════════╣'));
  console.log(chalk.cyan('║ ') + chalk.gray('Type "help" for commands, "exit" to quit'.padEnd(innerWidth)) + chalk.cyan('║'));
  console.log(chalk.cyan('╚═══════════════════════════════════════════════════════════╝'));
  console.log();
}

function printHelp() {
  console.log(chalk.cyan('\n📚 Available Commands:'));
  console.log(chalk.white('  help      - Show this help message'));
  console.log(chalk.white('  clear     - Clear the screen'));
  console.log(chalk.white('  history   - Show command history'));
  console.log(chalk.white('  exit/quit - Exit the debug session'));
  console.log();
}

export async function runDebugSession(
  apiUrl: string,
  apiKey?: string,
  clusterId?: string
): Promise<void> {
  log('=== runDebugSession STARTED ===');
  log('Environment', {
    'stdin.isTTY': process.stdin.isTTY,
    'stdout.isTTY': process.stdout.isTTY,
    'stdin.readable': process.stdin.readable,
    'apiUrl': apiUrl,
    'hasApiKey': !!apiKey
  });
  
  const session = new KubentlyA2ASession(apiUrl, apiKey, clusterId);
  let pendingOperation = false;
  let isClosing = false;
  let eventCount = 0;
  
  printWelcome();
  
  return new Promise<void>((resolve) => {
    log('Creating Promise wrapper');
    
    // Check stdin status before creating readline
    log('Pre-readline stdin state', {
      readable: process.stdin.readable,
      readableEnded: process.stdin.readableEnded,
      destroyed: process.stdin.destroyed,
      closed: (process.stdin as any).closed,
      isPaused: process.stdin.isPaused()
    });
    
    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout,
      prompt: chalk.magenta('kubently> '),
      terminal: true,
      historySize: 100,
      removeHistoryDuplicates: true,
    });
    
    log('Readline interface created');
    
    // Add error handlers
    process.stdin.on('error', (err) => {
      log('stdin error', err);
    });
    
    process.stdin.on('end', () => {
      log('stdin end event');
    });
    
    process.stdin.on('close', () => {
      log('stdin close event');
    });
    
    // Monitor readline internal state
    setInterval(() => {
      log('Periodic state check', {
        pendingOperation,
        isClosing,
        eventCount,
        'stdin.readable': process.stdin.readable,
        'stdin.isPaused': process.stdin.isPaused(),
        'rl.terminal': rl.terminal,
        'rl.paused': (rl as any).paused,
        'rl.closed': (rl as any).closed
      });
    }, 5000);
    
    process.stdin.resume();
    log('process.stdin.resume() called');
    
    const keepAliveInterval = setInterval(() => {
      log('Keepalive tick');
    }, 10000);
    
    rl.prompt();
    log('Initial prompt shown');

    const processCommand = async (command: string) => {
      log(`processCommand called: "${command}"`);
      
      if (pendingOperation) {
        log('Operation already pending');
        console.log(chalk.yellow('⚠️  Please wait for the current operation to complete.'));
        return;
      }
      
      pendingOperation = true;
      log('pendingOperation = true');
      
      const spinner = ora('Waiting for Kubently agent...').start();
      
      try {
        log('Sending message to session');
        const result = await session.sendMessage(command);
        log('Response received', { success: result.success, hasOutput: !!result.output });
        spinner.succeed('Agent responded');
        
        if (result.success && result.output) {
          console.log(chalk.green('\n🤖 Kubently Response:'));
          console.log(chalk.white('─'.repeat(60)));
          console.log(result.output);
          console.log(chalk.white('─'.repeat(60)));
          console.log();
        } else {
          console.log(chalk.red(`\n⚠️ Error: ${result.error || 'Command failed'}`));
        }
      } catch (error) {
        log('Error in processCommand', error);
        spinner.fail('Operation failed');
        console.log(chalk.red(`\n⚠️ Error: ${error instanceof Error ? error.message : 'Unknown error'}`));
      } finally {
        log('processCommand finally block');
        pendingOperation = false;
        log('pendingOperation = false');
        
        if (isClosing) {
          log('isClosing=true, closing readline');
          rl.close();
        } else {
          log('Showing prompt again');
          rl.prompt();
          log('Prompt shown');
        }
      }
      
      log('processCommand completed');
    };

    rl.on('line', (line: string) => {
      eventCount++;
      log(`'line' event #${eventCount}: "${line}"`);
      
      const command = line.trim();
      
      if (!command) {
        log('Empty command');
        if (!isClosing) rl.prompt();
        return;
      }

      switch (command.toLowerCase()) {
        case 'exit':
        case 'quit':
          log('Exit command');
          console.log(chalk.green('\n👋 Goodbye!'));
          isClosing = true;
          if (!pendingOperation) {
            log('No pending operation, closing immediately');
            rl.close();
          } else {
            log('Pending operation, will close when done');
          }
          return;
        case 'help':
          log('Help command');
          printHelp();
          if (!isClosing) rl.prompt();
          return;
        case 'clear':
          log('Clear command');
          console.clear();
          printWelcome();
          if (!isClosing) rl.prompt();
          return;
      }

      log('Regular command, using setImmediate');
      setImmediate(() => {
        log('setImmediate callback executing');
        processCommand(command);
      });
      log('line handler completed synchronously');
    });

    rl.on('SIGINT', () => {
      log('SIGINT received');
      console.log(chalk.yellow('\n\n👋 Goodbye!'));
      rl.close();
    });

    rl.on('close', () => {
      log('readline close event');
      isClosing = true;
      process.stdin.pause();
      clearInterval(keepAliveInterval);
      log('Resolving promise');
      resolve();
    });
    
    log('All event handlers attached');
  });
}

export function debugCommand(config: Config): Command {
  const cmd = new Command('debug');
  
  cmd
    .description('🐛 Start interactive debugging session - ULTRA DIAGNOSTIC')
    .argument('[cluster-id]', 'Cluster ID to debug (optional)')
    .action(async (clusterId?: string) => {
      log('debugCommand action started');
      
      const apiUrl = config.getApiUrl();
      
      if (!apiUrl) {
        console.log(chalk.red('✗ API URL is required.'));
        process.exit(1);
      }
      
      const authMethod = config.getAuthMethod();
      let apiKey: string | undefined;
      
      if (authMethod === 'api_key') {
        apiKey = config.getApiKey();
        if (!apiKey) {
          console.log(chalk.red('✗ API key is required.'));
          process.exit(1);
        }
      }
      
      try {
        const a2aPath = config.getA2aPath() || '/a2a';
        const debugApiUrl = apiUrl.replace(/\/$/, '') + a2aPath + '/';
        
        log('Starting debug session');
        await runDebugSession(debugApiUrl, apiKey, clusterId);
        log('Debug session completed');
        console.log(chalk.green('\n✓ Session ended'));
      } catch (error) {
        log('Error in debug command', error);
        console.log(chalk.red(`✗ Failed: ${error instanceof Error ? error.message : 'Unknown error'}`));
        process.exit(1);
      }
    });
  
  return cmd;
}