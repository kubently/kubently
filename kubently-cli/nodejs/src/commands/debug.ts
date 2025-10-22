import { Command } from 'commander';
import chalk from 'chalk';
import * as readline from 'readline';
import ora from 'ora';
import { Config } from '../lib/config.js';
import { KubentlyA2ASession } from '../lib/a2aClient.js';

function printWelcome() {
  console.log(chalk.cyan('\n╔═══════════════════════════════════════════════════════════╗'));
  console.log(chalk.cyan('║') + chalk.white('           🚀 Kubently Debug Session                       ') + chalk.cyan('║'));
  console.log(chalk.cyan('╠═══════════════════════════════════════════════════════════╣'));
  console.log(chalk.cyan('║ ') + chalk.white('Mode:       ') + chalk.green('Agent Chat (A2A Protocol)'.padEnd(45)) + chalk.cyan(' ║'));
  console.log(chalk.cyan('╠═══════════════════════════════════════════════════════════╣'));
  console.log(chalk.cyan('║ ') + chalk.gray('Type "help" for commands, "clear" to start new convo'.padEnd(57)) + chalk.cyan(' ║'));
  console.log(chalk.cyan('╚═══════════════════════════════════════════════════════════╝'));
  console.log();
}

function printHelp() {
  console.log(chalk.cyan('\n📚 Available Commands:'));
  console.log(chalk.white('  help      - Show this help message'));
  console.log(chalk.white('  clear     - Clear screen and start new conversation'));
  console.log(chalk.white('  history   - Show command history'));
  console.log(chalk.white('  exit/quit - Exit the debug session'));
  console.log(chalk.white('\nYou can ask the agent to perform operations using natural language.'));
  console.log(chalk.gray('Examples:'));
  console.log(chalk.gray('  What pods are running in the default namespace?'));
  console.log(chalk.gray('  Show me the logs for pod nginx'));
  console.log();
}

export async function runDebugSession(
  apiUrl: string,
  apiKey?: string,
  clusterId?: string,
  insecure: boolean = false
): Promise<void> {
  const session = new KubentlyA2ASession(apiUrl, apiKey, clusterId, insecure);
  let pendingOperation = false;
  let isClosing = false;
  
  printWelcome();
  
  return new Promise<void>((resolve) => {
    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout,
      prompt: chalk.magenta('kubently> '),
      terminal: true,
      historySize: 100,
      removeHistoryDuplicates: true,
    });
    
    // CRITICAL: Keep the event loop alive so Node.js doesn't exit prematurely
    // after an async operation completes.
    process.stdin.resume();
    const keepAliveInterval = setInterval(() => {}, 1000 * 60 * 60);
    
    // Helper function to ensure readline continues to work
    const ensureReadlineActive = () => {
      if (!isClosing && !pendingOperation) {
        // Resume stdin if it was paused
        if (process.stdin.isPaused()) {
          process.stdin.resume();
        }
        // Force readline to re-prompt
        setImmediate(() => {
          rl.prompt();
        });
      }
    };
    
    rl.prompt();

    rl.on('line', (line: string) => {
      const command = line.trim();
      
      if (!command) {
        if (!isClosing) rl.prompt();
        return;
      }

      switch (command.toLowerCase()) {
        case 'exit':
        case 'quit':
          console.log(chalk.green('\n👋 Goodbye!'));
          isClosing = true;
          if (!pendingOperation) rl.close();
          return;
        case 'clear':
          console.clear();
          session.resetSession();
          console.log(chalk.green('✨ Started new conversation (session reset)'));
          printWelcome();
          if (!isClosing) rl.prompt();
          return;
        case 'help':
          printHelp();
          if (!isClosing) rl.prompt();
          return;
        case 'history':
           console.log(chalk.cyan('\n📜 Command History:'));
           (rl as any).history.slice().reverse().forEach((cmd: string, idx: number) => {
             console.log(chalk.gray(`  ${idx + 1}. ${cmd}`));
           });
           console.log();
           if (!isClosing) rl.prompt();
           return;
      }

      // CRITICAL: Don't process if already processing
      if (pendingOperation) {
        console.log(chalk.yellow('⚠️  Please wait for the current operation to complete.'));
        return;
      }

      // Mark as processing immediately
      pendingOperation = true;
      
      // Process in next tick to let event handler complete
      setImmediate(async () => {
        const spinner = ora('Waiting for Kubently agent...').start();

        try {
          const result = await session.sendMessage(command);

          if (result.success && result.output) {
            spinner.succeed('Agent responded');
            console.log(chalk.green('\n🤖 Kubently Response:'));
            console.log(chalk.white('─'.repeat(60)));
            console.log(result.output);
            console.log(chalk.white('─'.repeat(60)));
            console.log();
          } else {
            spinner.fail('Operation failed');
            console.log(chalk.red(`\n⚠️ Error: ${result.error || 'No response from server'}`));
          }
        } catch (error) {
          spinner.fail('Operation failed');
          console.log(chalk.red(`\n⚠️ Error: ${error instanceof Error ? error.message : 'Unknown error'}`));
        } finally {
          // CRITICAL: Always reset the flag and re-prompt
          pendingOperation = false;
          if (isClosing) {
            rl.close();
          } else {
            // Ensure readline is active and ready for next input
            ensureReadlineActive();
          }
        }
      });
    });

    rl.on('SIGINT', () => {
      console.log(chalk.yellow('\n\n👋 Goodbye!'));
      rl.close();
    });

    rl.on('close', () => {
      isClosing = true;
      process.stdin.pause(); // Allow the process to exit
      clearInterval(keepAliveInterval);
      resolve();
    });
  });
}

// The debugCommand function remains the same as your existing one.
export function debugCommand(config: Config): Command {
  const cmd = new Command('debug');
  
  cmd
    .description('🐛 Start interactive debugging session (A2A protocol)')
    .argument('[cluster-id]', 'Cluster ID to debug (optional)')
    .option('--insecure', 'Disable SSL certificate verification (for testing only)')
    .action(async (clusterId?: string, options?: any) => {
      const apiUrl = config.getApiUrl();
      
      if (!apiUrl) {
        console.log(chalk.red('✗ API URL is required.'));
        console.log(chalk.yellow('Run "kubently init" or set environment variables.'));
        process.exit(1);
      }
      
      const authMethod = config.getAuthMethod();
      let apiKey: string | undefined;
      
      if (authMethod === 'api_key') {
        apiKey = config.getApiKey();
        if (!apiKey) {
          console.log(chalk.red('✗ API key is required for authentication.'));
          console.log(chalk.yellow('Run "kubently configure" or "kubently login --api-key <key>".'));
          process.exit(1);
        }
      } else if (authMethod === 'oauth') {
        const tokens = config.getOAuthTokens();
        if (!tokens || !tokens.access_token) {
          console.log(chalk.red('✗ OAuth authentication required.'));
          console.log(chalk.yellow('Run "kubently login" to authenticate.'));
          process.exit(1);
        }
        if (config.isTokenExpired()) {
          console.log(chalk.red('✗ OAuth token expired.'));
          console.log(chalk.yellow('Run "kubently login" to re-authenticate.'));
          process.exit(1);
        }
      }
      
      try {
        const a2aPath = config.getA2aPath() || '/a2a';
        const debugApiUrl = apiUrl.replace(/\/$/, '') + a2aPath + '/';

        if (options?.insecure) {
          console.log(chalk.yellow('⚠️  Warning: SSL certificate verification disabled (insecure mode)'));
        }

        await runDebugSession(debugApiUrl, apiKey, clusterId, options?.insecure || false);
        console.log(chalk.green('\n✓ Session ended'));
      } catch (error) {
        console.log(chalk.red(`✗ Failed to start debug session: ${error instanceof Error ? error.message : 'Unknown error'}`));
        process.exit(1);
      }
    });
  
  return cmd;
}