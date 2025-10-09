import chalk from 'chalk';
import inquirer from 'inquirer';
import ora from 'ora';
import { Config } from '../lib/config.js';
import { KubentlyAdminClient } from '../lib/adminClient.js';

async function listClusters(client: KubentlyAdminClient): Promise<void> {
  const spinner = ora('Fetching clusters...').start();
  
  try {
    const result = await client.listClusters();
    const clusters = result.clusters || [];
    
    spinner.stop();
    
    if (clusters.length === 0) {
      console.log(chalk.yellow('\n📋 No clusters registered'));
      return;
    }
    
    console.log(chalk.cyan('\n📋 Registered Clusters:'));
    console.log(chalk.white('─'.repeat(60)));
    
    clusters.forEach(cluster => {
      const status = cluster.connected ? chalk.green('✓ Connected') : chalk.red('✗ Disconnected');
      const lastSeen = cluster.lastSeen ? chalk.gray(` (Last seen: ${cluster.lastSeen})`) : '';
      console.log(`  ${chalk.white(cluster.id)} - ${status}${lastSeen}`);
    });
    
    console.log(chalk.white('─'.repeat(60)));
    console.log();
  } catch (error) {
    spinner.fail('Failed to list clusters');
    console.log(chalk.red(`✗ Error: ${error instanceof Error ? error.message : 'Unknown error'}`));
  }
}

async function addCluster(client: KubentlyAdminClient): Promise<void> {
  const { clusterId } = await inquirer.prompt([
    {
      type: 'input',
      name: 'clusterId',
      message: 'Enter cluster ID:',
      validate: (input) => input.trim() !== '' || 'Cluster ID is required'
    }
  ]);

  const spinner = ora('Creating agent token...').start();
  
  try {
    const result = await client.createAgentToken(clusterId);
    
    spinner.succeed('Agent token created successfully');
    
    console.log(chalk.cyan('\n🔑 Agent Configuration:'));
    console.log(chalk.white('─'.repeat(60)));
    console.log(chalk.white('Cluster ID:  ') + chalk.green(result.clusterId));
    console.log(chalk.white('Token:       ') + chalk.yellow(result.token));
    console.log(chalk.white('Created At:  ') + chalk.gray(result.createdAt));
    console.log(chalk.white('─'.repeat(60)));
    
    console.log(chalk.cyan('\n📝 Installation Instructions:'));
    console.log(chalk.white('1. Save the token securely'));
    console.log(chalk.white('2. Deploy the Kubently agent to your cluster'));
    console.log(chalk.white('3. Configure the agent with this token'));
    console.log();
  } catch (error) {
    spinner.fail('Failed to create agent token');
    console.log(chalk.red(`✗ Error: ${error instanceof Error ? error.message : 'Unknown error'}`));
  }
}

async function removeCluster(client: KubentlyAdminClient): Promise<void> {
  const spinner = ora('Fetching clusters...').start();
  
  try {
    const result = await client.listClusters();
    const clusters = result.clusters || [];
    
    spinner.stop();
    
    if (clusters.length === 0) {
      console.log(chalk.yellow('No clusters to remove'));
      return;
    }
    
    const { clusterId } = await inquirer.prompt([
      {
        type: 'list',
        name: 'clusterId',
        message: 'Select cluster to remove:',
        choices: clusters.map(c => ({
          name: `${c.id} ${c.connected ? chalk.green('✓') : chalk.red('✗')}`,
          value: c.id
        }))
      }
    ]);
    
    const { confirm } = await inquirer.prompt([
      {
        type: 'confirm',
        name: 'confirm',
        message: `Are you sure you want to remove cluster "${clusterId}"?`,
        default: false
      }
    ]);
    
    if (!confirm) {
      console.log(chalk.yellow('Operation cancelled'));
      return;
    }
    
    const revokeSpinner = ora('Revoking agent token...').start();
    
    try {
      await client.revokeAgentToken(clusterId);
      revokeSpinner.succeed(`Cluster "${clusterId}" removed successfully`);
    } catch (error) {
      revokeSpinner.fail('Failed to revoke agent token');
      console.log(chalk.red(`✗ Error: ${error instanceof Error ? error.message : 'Unknown error'}`));
    }
  } catch (error) {
    spinner.fail('Failed to list clusters');
    console.log(chalk.red(`✗ Error: ${error instanceof Error ? error.message : 'Unknown error'}`));
  }
}

async function viewClusterStatus(client: KubentlyAdminClient): Promise<void> {
  const spinner = ora('Fetching clusters...').start();
  
  try {
    const result = await client.listClusters();
    const clusters = result.clusters || [];
    
    spinner.stop();
    
    if (clusters.length === 0) {
      console.log(chalk.yellow('No clusters available'));
      return;
    }
    
    const { clusterId } = await inquirer.prompt([
      {
        type: 'list',
        name: 'clusterId',
        message: 'Select cluster to view status:',
        choices: clusters.map(c => ({
          name: `${c.id} ${c.connected ? chalk.green('✓') : chalk.red('✗')}`,
          value: c.id
        }))
      }
    ]);
    
    const statusSpinner = ora('Fetching cluster status...').start();
    
    try {
      const status = await client.getClusterStatus(clusterId);
      
      statusSpinner.stop();
      
      console.log(chalk.cyan('\n📊 Cluster Status:'));
      console.log(chalk.white('─'.repeat(60)));
      console.log(chalk.white('Cluster ID:          ') + chalk.green(status.id));
      console.log(chalk.white('Status:              ') + (status.connected ? chalk.green('Connected') : chalk.red('Disconnected')));
      console.log(chalk.white('Agent Status:        ') + chalk.yellow(status.status));
      if (status.lastSeen) {
        console.log(chalk.white('Last Seen:           ') + chalk.gray(status.lastSeen));
      }
      if (status.version) {
        console.log(chalk.white('Agent Version:       ') + chalk.blue(status.version));
      }
      if (status.kubernetesVersion) {
        console.log(chalk.white('Kubernetes Version:  ') + chalk.blue(status.kubernetesVersion));
      }
      console.log(chalk.white('─'.repeat(60)));
      console.log();
    } catch (error) {
      statusSpinner.fail('Failed to get cluster status');
      console.log(chalk.red(`✗ Error: ${error instanceof Error ? error.message : 'Unknown error'}`));
    }
  } catch (error) {
    spinner.fail('Failed to list clusters');
    console.log(chalk.red(`✗ Error: ${error instanceof Error ? error.message : 'Unknown error'}`));
  }
}

export async function runAdminMenu(config: Config): Promise<void> {
  const apiUrl = config.getApiUrl();
  const apiKey = config.getApiKey();
  
  if (process.env.DEBUG === 'true') {
    console.log('DEBUG: runAdminMenu apiUrl:', apiUrl);
  }
  
  if (!apiUrl || !apiKey) {
    console.log(chalk.red('✗ API URL and API key are required.'));
    console.log(chalk.yellow('Run "kubently init" or set environment variables.'));
    return;
  }
  
  const client = new KubentlyAdminClient(apiUrl, apiKey);
  
  while (true) {
    console.log(chalk.cyan('\n╔═══════════════════════════════════════════════════════════╗'));
    console.log(chalk.cyan('║') + chalk.white('           ⚙️  Kubently Admin Operations                   ') + chalk.cyan('║'));
    console.log(chalk.cyan('╚═══════════════════════════════════════════════════════════╝'));
    
    const { action } = await inquirer.prompt([
      {
        type: 'list',
        name: 'action',
        message: 'Select an action:',
        choices: [
          { name: '📋 List Clusters', value: 'list' },
          { name: '➕ Add Cluster', value: 'add' },
          { name: '➖ Remove Cluster', value: 'remove' },
          { name: '📊 View Cluster Status', value: 'status' },
          { name: '🔙 Back to Main Menu', value: 'back' },
          { name: '❌ Exit', value: 'exit' }
        ]
      }
    ]);
    
    switch (action) {
      case 'list':
        await listClusters(client);
        break;
      case 'add':
        await addCluster(client);
        break;
      case 'remove':
        await removeCluster(client);
        break;
      case 'status':
        await viewClusterStatus(client);
        break;
      case 'back':
        return;
      case 'exit':
        console.log(chalk.green('\n👋 Goodbye!'));
        process.exit(0);
    }
  }
}