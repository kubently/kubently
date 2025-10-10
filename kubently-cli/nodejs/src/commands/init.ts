import { Command } from 'commander';
import chalk from 'chalk';
import inquirer from 'inquirer';
import ora from 'ora';
import { Config } from '../lib/config.js';
import { KubentlyAdminClient } from '../lib/adminClient.js';

export function initCommand(config: Config): Command {
  const cmd = new Command('init');
  
  cmd
    .description('🔧 Initialize Kubently CLI configuration')
    .action(async () => {
      console.log(chalk.cyan('╔═══════════════════════════════════════════╗'));
      console.log(chalk.cyan('║') + chalk.white('    🔧 Kubently CLI Configuration         ') + chalk.cyan('║'));
      console.log(chalk.cyan('╠═══════════════════════════════════════════╣'));
      console.log(chalk.cyan('║') + chalk.gray(' Let\'s set up your connection to Kubently ') + chalk.cyan('║'));
      console.log(chalk.cyan('╚═══════════════════════════════════════════╝'));
      console.log();

      const answers = await inquirer.prompt([
        {
          type: 'input',
          name: 'apiUrl',
          message: '💬 Enter Kubently API URL:',
          default: config.getApiUrl() || 'http://localhost:8000',
          validate: (input) => {
            if (!input) return 'API URL is required';
            return true;
          }
        },
        {
          type: 'password',
          name: 'apiKey',
          message: '🔐 Enter Admin API Key:',
          mask: '*',
          validate: (input) => {
            if (!input) return 'API Key is required';
            return true;
          }
        }
      ]);

      // Save configuration
      config.setApiUrl(answers.apiUrl);
      config.setApiKey(answers.apiKey);
      config.save();

      console.log(chalk.green(`\n✓ Configuration saved to ~/.kubently/config.json`));

      // Test connection
      const spinner = ora('Testing connection to Kubently API...').start();
      
      try {
        const client = new KubentlyAdminClient(answers.apiUrl, answers.apiKey);
        const connected = await client.testConnection();
        
        if (connected) {
          spinner.succeed('Successfully connected to Kubently API');
          console.log(chalk.green('\n✨ You\'re all set! Use "kubently --help" to see available commands.'));
        } else {
          spinner.fail('Could not connect to API');
          console.log(chalk.yellow('\n⚠ Please check your API URL and try again.'));
        }
      } catch (error) {
        spinner.fail('Connection test failed');
        console.log(chalk.red(`\n✗ Error: ${error instanceof Error ? error.message : 'Unknown error'}`));
        console.log(chalk.yellow('\n⚠ Please verify your settings and try again.'));
      }
    });

  return cmd;
}