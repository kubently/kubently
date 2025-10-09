import { Command } from 'commander';
import chalk from 'chalk';
import ora from 'ora';
import inquirer from 'inquirer';
import fs from 'fs';
import { Config } from '../lib/config.js';
import { KubentlyAdminClient } from '../lib/adminClient.js';
import { 
  generateAgentDeployment, 
  generateDockerCompose,
  generateHelmValues,
  generateShellScript 
} from '../lib/templates.js';

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
    .description('🆕 Register a new cluster with Kubently')
    .option('-o, --output <format>', 'Output format: k8s, docker, helm, script, or all', 'k8s')
    .option('-n, --namespace <namespace>', 'Kubernetes namespace', 'kubently')
    .option('-i, --image <image>', 'Agent Docker image', 'kubently/agent:latest')
    .action(async (clusterId: string, options) => {
      if (!ensureApiConfig(config)) return;

      const spinner = ora(`Creating token for cluster '${clusterId}'...`).start();
      
      try {
        const client = new KubentlyAdminClient(config.getApiUrl()!, config.getApiKey()!);
        const result = await client.createAgentToken(clusterId);
        const token = result.token;
        
        // Store in config
        config.addCluster(clusterId, {
          token,
          createdAt: new Date().toISOString(),
          namespace: options.namespace
        });
        
        spinner.succeed(`Token created and stored for cluster '${clusterId}'`);
        
        const apiUrl = config.getApiUrl()!;
        const outputs = options.output.split(',').map((o: string) => o.trim());
        
        // Generate manifests based on output format
        if (outputs.includes('k8s') || outputs.includes('all')) {
          const manifest = generateAgentDeployment(
            clusterId,
            token,
            apiUrl,
            options.namespace,
            options.image
          );
          const filename = `kubently-agent-${clusterId}.yaml`;
          fs.writeFileSync(filename, manifest);
          console.log(chalk.green(`✓ Kubernetes manifest written to ${filename}`));
          console.log(chalk.gray(`\n  Deploy to Kubernetes with:`));
          console.log(chalk.cyan(`  kubectl apply -f ${filename}\n`));
        }
        
        if (outputs.includes('docker') || outputs.includes('all')) {
          const compose = generateDockerCompose(clusterId, token, apiUrl);
          const filename = `docker-compose-${clusterId}.yaml`;
          fs.writeFileSync(filename, compose);
          console.log(chalk.green(`✓ Docker Compose file written to ${filename}`));
          console.log(chalk.gray(`\n  Run locally with:`));
          console.log(chalk.cyan(`  docker-compose -f ${filename} up\n`));
        }
        
        if (outputs.includes('helm') || outputs.includes('all')) {
          const values = generateHelmValues(
            clusterId,
            token,
            apiUrl,
            options.namespace,
            options.image
          );
          const filename = `helm-values-${clusterId}.yaml`;
          fs.writeFileSync(filename, values);
          console.log(chalk.green(`✓ Helm values file written to ${filename}`));
          console.log(chalk.gray(`\n  Deploy with Helm:`));
          console.log(chalk.cyan(`  helm install kubently-agent ./chart -f ${filename}\n`));
        }
        
        if (outputs.includes('script') || outputs.includes('all')) {
          const script = generateShellScript(clusterId, token, apiUrl, options.namespace);
          const filename = `deploy-${clusterId}.sh`;
          fs.writeFileSync(filename, script, { mode: 0o755 });
          console.log(chalk.green(`✓ Deployment script written to ${filename}`));
          console.log(chalk.gray(`\n  Deploy with:`));
          console.log(chalk.cyan(`  ./${filename}\n`));
        }
        
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
          const lastSeen = cluster.lastSeen || 'Never';
          
          console.log(chalk.cyan('║ ') + 
            chalk.white('ID: ') + chalk.yellow(cluster.id.padEnd(20)) + 
            ' ' + status.padEnd(25) + 
            chalk.gray(` Last: ${lastSeen}`.padEnd(20)) + 
            chalk.cyan(' ║'));
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
        console.log(chalk.cyan('║ ') + chalk.white('Last Seen:  ') + chalk.gray((status.lastSeen || 'Never').padEnd(46)) + chalk.cyan('║'));
        console.log(chalk.cyan('║ ') + chalk.white('Version:    ') + chalk.gray((status.version || 'Unknown').padEnd(46)) + chalk.cyan('║'));
        console.log(chalk.cyan('║ ') + chalk.white('K8s Version:') + chalk.gray((status.kubernetesVersion || 'Unknown').padEnd(46)) + chalk.cyan('║'));
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
        console.log(chalk.yellow('\n⚠ Remember to remove the agent from the cluster:'));
        console.log(chalk.cyan('  kubectl -n kubently delete deployment kubently-agent'));
        
      } catch (error) {
        spinner.fail('Failed to remove cluster');
        console.log(chalk.red(`✗ Error: ${error instanceof Error ? error.message : 'Unknown error'}`));
        process.exit(1);
      }
    });

  return cluster;
}