import { Command } from 'commander';
import chalk from 'chalk';
import inquirer from 'inquirer';
import { Config } from '../lib/config.js';
import { runDebugSession } from './debug.js';
import { runAdminMenu } from './admin.js';

export async function runInteractiveMode(config: Config): Promise<void> {
  const baseApiUrl = config.getApiUrl();  // Keep the base URL
  const apiKey = config.getApiKey();
  
  if (process.env.DEBUG === 'true') {
    console.log('DEBUG: runInteractiveMode called');
    console.log('DEBUG: baseApiUrl:', baseApiUrl);
    console.log('DEBUG: apiKey:', apiKey ? '***' : 'not set');
  }
  
  if (!baseApiUrl || !apiKey) {
    console.log(chalk.red('✗ API URL and API key are required.'));
    console.log(chalk.yellow('Run "kubently init" or set environment variables.'));
    process.exit(1);
  }

  // Main loop - keep running until user explicitly exits
  let shouldContinue = true;
  
  while (shouldContinue) {
    console.log(); // Add spacing

    // Get user's choice
    const { mode } = await inquirer.prompt([
      {
        type: 'list',
        name: 'mode',
        message: 'Select operation mode:',
        choices: [
          {
            name: '🐛 Debug Operations (Chat with Kubernetes agent)',
            value: 'debug',
            short: 'Debug'
          },
          {
            name: '⚙️  Admin Operations (Manage clusters and agents)',
            value: 'admin',
            short: 'Admin'
          },
          {
            name: '❌ Exit',
            value: 'exit',
            short: 'Exit'
          }
        ]
      }
    ]);

    // Process the choice
    switch (mode) {
      case 'debug':
        const a2aPath = config.getA2aPath() || '/a2a';
        // Ensure trailing slash for A2A endpoint
        const debugApiUrl = baseApiUrl.replace(/\/$/, '') + a2aPath + '/';
        await runDebugSession(debugApiUrl, apiKey);
        // After debug session completes, loop will continue and show menu again
        break;
        
      case 'admin':
        // Admin operations should use the base URL without /a2a
        await runAdminMenu(config);
        // After admin menu completes, loop will continue and show menu again
        break;
        
      case 'exit':
        shouldContinue = false;
        break;
    }
  }
  
  console.log(chalk.green('\n👋 Goodbye!'));
  // Don't call process.exit() here - let the function return naturally
}