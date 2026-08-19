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

async function addCluster(client: KubentlyAdminClient, apiUrl: string): Promise<void> {
  const { clusterId } = await inquirer.prompt([
    {
      type: 'input',
      name: 'clusterId',
      message: 'Enter cluster ID:',
      validate: (input) => input.trim() !== '' || 'Cluster ID is required'
    }
  ]);

  const { useCustomToken } = await inquirer.prompt([
    {
      type: 'confirm',
      name: 'useCustomToken',
      message: 'Use custom token (from Vault, etc.)?',
      default: false
    }
  ]);

  let customToken: string | undefined;
  if (useCustomToken) {
    const response = await inquirer.prompt([
      {
        type: 'input',
        name: 'token',
        message: 'Enter custom token:',
        validate: (input) => input.trim() !== '' || 'Token is required'
      }
    ]);
    customToken = response.token;
  }

  const spinner = ora('Creating executor token...').start();

  try {
    const result = await client.createAgentToken(clusterId, customToken);
    const token = result.token;

    spinner.succeed('Executor token created successfully');

    console.log(chalk.cyan('\n╔═══════════════════════════════════════════════════════════╗'));
    console.log(chalk.cyan('║') + chalk.white('              Executor Token Created                       ') + chalk.cyan('║'));
    console.log(chalk.cyan('╠═══════════════════════════════════════════════════════════╣'));
    console.log(chalk.cyan('║ ') + chalk.gray('Token:'.padEnd(57)) + chalk.cyan(' ║'));
    console.log(chalk.cyan('║ ') + chalk.yellow(token.substring(0, 57)) + chalk.cyan('║'));
    if (token.length > 57) {
      console.log(chalk.cyan('║ ') + chalk.yellow(token.substring(57).padEnd(57)) + chalk.cyan('║'));
    }
    console.log(chalk.cyan('╚═══════════════════════════════════════════════════════════╝'));

    console.log(chalk.cyan('\n📦 Deploy Executor to Cluster:\n'));

    console.log(chalk.white('1. Get the Helm chart:'));
    console.log(chalk.gray('   # Option A: Clone the repository'));
    console.log(chalk.gray('   git clone https://github.com/kubently/kubently.git\n'));
    console.log(chalk.gray('   # Option B: Download from GitHub releases'));
    console.log(chalk.gray('   # https://github.com/kubently/kubently/releases\n'));

    console.log(chalk.white('2. Create secret on the executor cluster:'));
    console.log(chalk.gray('   kubectl create secret generic kubently-executor-token \\'));
    console.log(chalk.gray(`     --from-literal=token="${token}" \\`));
    console.log(chalk.gray('     --namespace kubently\n'));

    console.log(chalk.white('3. Deploy executor using Helm:'));
    console.log(chalk.gray('   # If you cloned the repo:'));
    console.log(chalk.gray('   helm install kubently ./deployment/helm/kubently \\'));
    console.log(chalk.gray('     --set api.enabled=false \\'));
    console.log(chalk.gray('     --set redis.enabled=false \\'));
    console.log(chalk.gray('     --set executor.enabled=true \\'));
    console.log(chalk.gray(`     --set executor.clusterId=${clusterId} \\`));
    console.log(chalk.gray(`     --set executor.apiUrl=${apiUrl} \\`));
    console.log(chalk.gray('     --set executor.existingSecret=kubently-executor-token \\'));
    console.log(chalk.gray('     --namespace kubently\n'));

    console.log(chalk.yellow('⚠️  Store the token securely - it cannot be retrieved later!'));
    console.log();
  } catch (error) {
    spinner.fail('Failed to create executor token');
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
        type: 'select',
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
        type: 'select',
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
        type: 'select',
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
        await addCluster(client, apiUrl);
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