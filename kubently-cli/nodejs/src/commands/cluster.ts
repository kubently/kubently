import { Command } from 'commander';
import chalk from 'chalk';
import ora from 'ora';
import inquirer from 'inquirer';
import { Config } from '../lib/config.js';
import { KubentlyAdminClient } from '../lib/adminClient.js';

function ensureApiConfig(config: Config): boolean {
  const apiUrl = config.getApiUrl();
  const apiKey = config.getApiKey();
  
  if (!apiUrl || !apiKey) {
    console.log(chalk.red('✗ API URL and API key are required.'));
    console.log(chalk.yellow('Run "kubently init" or set environment variables:'));
    console.log(chalk.gray('  export KUBENTLY_API_URL=http://your-api-url'));
    console.log(chalk.gray('  export KUBENTLY_API_KEY=your-api-key'));
    return false;
  }
  return true;
}

export function clusterCommands(config: Config): Command {
  const cluster = new Command('cluster');
  cluster.description('📦 Manage Kubently clusters');

  // Add cluster command
  cluster
    .command('add <cluster-id>')
    .description('🆕 Register a new cluster and get executor token')
    .option('--custom-token <token>', 'Provide a custom token (from Vault, etc.)')
    .action(async (clusterId: string, options) => {
      if (!ensureApiConfig(config)) return;

      const spinner = ora(`Creating token for cluster '${clusterId}'...`).start();

      try {
        const client = new KubentlyAdminClient(config.getApiUrl()!, config.getApiKey()!);
        const result = await client.createAgentToken(clusterId, options.customToken);
        const token = result.token;

        // Store in config
        config.addCluster(clusterId, {
          token,
          createdAt: new Date().toISOString(),
        });

        spinner.succeed(`Token created for cluster '${clusterId}'`);

        const apiUrl = config.getApiUrl()!;

        // Display token and deployment instructions
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

      } catch (error) {
        spinner.fail('Failed to add cluster');
        console.log(chalk.red(`✗ Error: ${error instanceof Error ? error.message : 'Unknown error'}`));
        process.exit(1);
      }
    });

  // List clusters command
  cluster
    .command('list')
    .description('📋 List all registered clusters')
    .action(async () => {
      if (!ensureApiConfig(config)) return;

      const spinner = ora('Fetching clusters...').start();
      
      try {
        const client = new KubentlyAdminClient(config.getApiUrl()!, config.getApiKey()!);
        const result = await client.listClusters();
        const clusters = result.clusters || [];
        
        spinner.stop();
        
        if (clusters.length === 0) {
          console.log(chalk.yellow('No clusters registered'));
          return;
        }
        
        console.log(chalk.cyan('\n╔═══════════════════════════════════════════════════════════╗'));
        console.log(chalk.cyan('║') + chalk.white('                  Registered Clusters                      ') + chalk.cyan('║'));
        console.log(chalk.cyan('╠═══════════════════════════════════════════════════════════╣'));
        
        clusters.forEach((cluster) => {
          const status = cluster.connected
            ? chalk.green('✓ Connected')
            : chalk.red('✗ Disconnected');

          console.log(chalk.cyan('║ ') +
            chalk.white('ID: ') + chalk.yellow(cluster.id.padEnd(20)) +
            ' ' + status.padEnd(36) +
            chalk.cyan('║'));
        });
        
        console.log(chalk.cyan('╚═══════════════════════════════════════════════════════════╝'));
        
      } catch (error) {
        spinner.fail('Failed to list clusters');
        console.log(chalk.red(`✗ Error: ${error instanceof Error ? error.message : 'Unknown error'}`));
        process.exit(1);
      }
    });

  // Status command
  cluster
    .command('status <cluster-id>')
    .description('🔍 Check status of a specific cluster')
    .action(async (clusterId: string) => {
      if (!ensureApiConfig(config)) return;

      const spinner = ora(`Checking status for '${clusterId}'...`).start();
      
      try {
        const client = new KubentlyAdminClient(config.getApiUrl()!, config.getApiKey()!);
        const status = await client.getClusterStatus(clusterId);
        
        spinner.stop();
        
        console.log(chalk.cyan('\n╔═══════════════════════════════════════════════════════════╗'));
        console.log(chalk.cyan('║') + chalk.white(`         Cluster Status: ${clusterId}`.padEnd(59)) + chalk.cyan('║'));
        console.log(chalk.cyan('╠═══════════════════════════════════════════════════════════╣'));
        console.log(chalk.cyan('║ ') + chalk.white('Status:     ') + (status.connected ? chalk.green('✓ Connected') : chalk.red('✗ Disconnected')).padEnd(58) + chalk.cyan('║'));
        console.log(chalk.cyan('║ ') + chalk.white('Version:    ') + chalk.gray((status.version || 'Unknown').padEnd(46)) + chalk.cyan('║'));

        // Display capability information if available
        if (status.mode) {
          console.log(chalk.cyan('╠═══════════════════════════════════════════════════════════╣'));
          console.log(chalk.cyan('║') + chalk.white('                    Capabilities                           ') + chalk.cyan('║'));
          console.log(chalk.cyan('╠═══════════════════════════════════════════════════════════╣'));

          const modeColor = status.mode === 'fullAccess' ? chalk.red :
                           status.mode === 'extendedReadOnly' ? chalk.yellow :
                           chalk.green;
          console.log(chalk.cyan('║ ') + chalk.white('Mode:       ') + modeColor(status.mode.padEnd(46)) + chalk.cyan('║'));

          if (status.capabilities) {
            const caps = status.capabilities;
            const features = Object.entries(caps.features || {})
              .filter(([_, enabled]) => enabled)
              .map(([name]) => name)
              .join(', ') || 'None';
            console.log(chalk.cyan('║ ') + chalk.white('Features:   ') + chalk.gray(features.padEnd(46)) + chalk.cyan('║'));
            console.log(chalk.cyan('║ ') + chalk.white('Verbs:      ') + chalk.gray((caps.allowed_verbs?.slice(0, 5).join(', ') + (caps.allowed_verbs?.length > 5 ? '...' : '')).padEnd(46)) + chalk.cyan('║'));
            if (caps.executor_pod) {
              console.log(chalk.cyan('║ ') + chalk.white('Pod:        ') + chalk.gray(caps.executor_pod.substring(0, 46).padEnd(46)) + chalk.cyan('║'));
            }
          }
        } else {
          console.log(chalk.cyan('║ ') + chalk.gray('Capabilities: Not reported (executor may need upgrade)'.padEnd(57)) + chalk.cyan('║'));
        }

        console.log(chalk.cyan('╚═══════════════════════════════════════════════════════════╝'));
        
      } catch (error) {
        spinner.fail('Failed to get cluster status');
        console.log(chalk.red(`✗ Error: ${error instanceof Error ? error.message : 'Unknown error'}`));
        process.exit(1);
      }
    });

  // Remove command
  cluster
    .command('remove <cluster-id>')
    .description('🗑️ Remove a cluster from Kubently')
    .option('-f, --force', 'Force removal without confirmation')
    .action(async (clusterId: string, options) => {
      if (!ensureApiConfig(config)) return;

      if (!options.force) {
        const { confirm } = await inquirer.prompt([
          {
            type: 'confirm',
            name: 'confirm',
            message: `Are you sure you want to remove cluster '${clusterId}'?`,
            default: false
          }
        ]);
        
        if (!confirm) {
          console.log(chalk.yellow('Cancelled'));
          return;
        }
      }

      const spinner = ora(`Removing cluster '${clusterId}'...`).start();
      
      try {
        const client = new KubentlyAdminClient(config.getApiUrl()!, config.getApiKey()!);
        await client.revokeAgentToken(clusterId);
        
        // Remove from local config
        config.removeCluster(clusterId);
        
        spinner.succeed(`Cluster '${clusterId}' removed successfully`);
        console.log(chalk.yellow('\n⚠ Remember to remove the executor from the cluster:'));
        console.log(chalk.cyan('  helm uninstall kubently --namespace kubently'));
        console.log(chalk.gray('  # Or if not using Helm:'));
        console.log(chalk.gray('  # kubectl -n kubently delete deployment kubently-executor'));
        
      } catch (error) {
        spinner.fail('Failed to remove cluster');
        console.log(chalk.red(`✗ Error: ${error instanceof Error ? error.message : 'Unknown error'}`));
        process.exit(1);
      }
    });

  return cluster;
}