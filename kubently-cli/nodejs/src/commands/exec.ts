import { Command } from 'commander';
import chalk from 'chalk';
import ora from 'ora';
import inquirer from 'inquirer';
import { Config } from '../lib/config.js';
import { KubentlyAdminClient } from '../lib/adminClient.js';

async function selectCluster(client: KubentlyAdminClient): Promise<string | null> {
  const spinner = ora('Fetching available clusters...').start();
  
  try {
    const result = await client.listClusters();
    const clusters = result.clusters || [];
    
    spinner.stop();
    
    if (clusters.length === 0) {
      console.log(chalk.yellow('No clusters are registered. Please register a cluster first.'));
      console.log(chalk.gray('Use: kubently cluster add <cluster-id>'));
      return null;
    }
    
    if (clusters.length === 1) {
      const clusterId = clusters[0].id;
      console.log(chalk.cyan(`Using the only available cluster: ${clusterId}`));
      return clusterId;
    }
    
    // Build choices for inquirer
    const choices = clusters.map(cluster => ({
      name: `${cluster.id} ${cluster.connected ? chalk.green('✓') : chalk.red('✗')}`,
      value: cluster.id,
      short: cluster.id
    }));
    
    const { clusterId } = await inquirer.prompt([
      {
        type: 'list',
        name: 'clusterId',
        message: 'Select a cluster:',
        choices,
        pageSize: 10
      }
    ]);
    
    return clusterId;
  } catch (error) {
    spinner.fail('Failed to list clusters');
    console.log(chalk.red(`✗ Error: ${error instanceof Error ? error.message : 'Unknown error'}`));
    return null;
  }
}

export function execCommand(config: Config): Command {
  const cmd = new Command('exec');
  
  cmd
    .description('⚡ Execute a single kubectl command on a cluster')
    .argument('<command...>', 'kubectl command to execute')
    .option('-c, --cluster <id>', 'Target cluster ID')
    .action(async (commandArgs: string[], options) => {
      const apiUrl = config.getApiUrl();
      const apiKey = config.getApiKey();
      
      if (!apiUrl || !apiKey) {
        console.log(chalk.red('✗ API URL and API key are required.'));
        console.log(chalk.yellow('Run "kubently init" or set environment variables.'));
        process.exit(1);
      }
      
      const client = new KubentlyAdminClient(apiUrl, apiKey);
      
      // Get cluster ID
      let clusterId = options.cluster;
      if (!clusterId) {
        clusterId = await selectCluster(client);
        if (!clusterId) {
          process.exit(1);
        }
      }
      
      const command = commandArgs.join(' ');
      const spinner = ora('Executing command...').start();
      
      try {
        if (process.env.DEBUG === 'true') {
          console.log(chalk.gray(`\nExecuting: kubectl ${command}`));
        }
        
        const result = await client.executeSingleCommand(clusterId, command);
        
        spinner.stop();
        
        if (result.success) {
          if (result.output) {
            console.log(result.output);
          }
        } else {
          console.log(chalk.red(`✗ Error: ${result.error || 'Command failed'}`));
          process.exit(1);
        }
      } catch (error) {
        spinner.fail('Command execution failed');
        console.log(chalk.red(`✗ Error: ${error instanceof Error ? error.message : 'Unknown error'}`));
        process.exit(1);
      }
    });
  
  return cmd;
}