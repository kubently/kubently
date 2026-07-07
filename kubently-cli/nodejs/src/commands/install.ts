import { Command } from 'commander';
import chalk from 'chalk';
import inquirer from 'inquirer';
import ora from 'ora';
import axios from 'axios';
import { Config } from '../lib/config.js';
import { KubentlyAdminClient } from '../lib/adminClient.js';
import { runDebugSession } from './debug.js';
import {
  buildHelmArgs,
  applySecret,
  currentContext,
  ensureNamespace,
  genToken,
  getSecretValue,
  preflight,
  providerEnvVar,
  restartExecutor,
  run,
  sanitizeClusterId,
  startPortForward,
  waitFor,
} from '../lib/installer.js';

const LOCAL_API_URL = 'http://localhost:8080';

export function installCommand(config: Config): Command {
  const cmd = new Command('install');

  cmd
    .description('🚀 Install Kubently into the current kubectl context and start a debug chat')
    .option('-n, --namespace <ns>', 'Namespace to install into', 'kubently')
    .option('--cluster-id <id>', 'Logical cluster id (default: derived from kube context)')
    .option(
      '--provider <provider>',
      'LLM provider: anthropic-claude | openai | google-gemini',
      'anthropic-claude'
    )
    .option('--llm-api-key <key>', 'LLM API key (default: provider env var, else prompt)')
    .option('--chart <path>', 'Install from a local chart directory instead of the published repo')
    .option('-y, --yes', 'Skip confirmation prompts', false)
    .option('--no-chat', 'Skip starting the interactive chat after install')
    .action(async (opts) => {
      try {
        await runInstall(config, opts);
      } catch (error) {
        console.error(
          chalk.red(`\n✗ Install failed: ${error instanceof Error ? error.message : error}`)
        );
        process.exit(1);
      }
    });

  return cmd;
}

interface InstallOpts {
  namespace: string;
  clusterId?: string;
  provider: string;
  llmApiKey?: string;
  chart?: string;
  yes: boolean;
  chat: boolean;
}

async function runInstall(config: Config, opts: InstallOpts): Promise<void> {
  preflight();
  const ctx = currentContext();
  const namespace = opts.namespace;
  const clusterId = opts.clusterId ?? sanitizeClusterId(ctx);

  console.log(chalk.cyan('\nKubently will be installed with:'));
  console.log(`  kube context : ${chalk.white(ctx)}`);
  console.log(`  namespace    : ${chalk.white(namespace)}`);
  console.log(`  cluster id   : ${chalk.white(clusterId)}`);
  console.log(`  LLM provider : ${chalk.white(opts.provider)}\n`);

  if (!opts.yes) {
    const { proceed } = await inquirer.prompt([
      { type: 'confirm', name: 'proceed', message: 'Continue?', default: true },
    ]);
    if (!proceed) return;
  }

  // LLM key: flag > env var > prompt
  const envVar = providerEnvVar(opts.provider);
  let llmKey = opts.llmApiKey ?? process.env[envVar];
  if (!llmKey) {
    const answer = await inquirer.prompt([
      {
        type: 'password',
        name: 'key',
        mask: '*',
        message: `Enter your ${envVar} (used by the agent to call the LLM):`,
        validate: (input: string) => (input ? true : 'API key is required'),
      },
    ]);
    llmKey = answer.key as string;
  }

  // 1. Namespace + secrets (idempotent; reuse existing api key so config keeps working)
  let spinner = ora('Creating namespace and secrets').start();
  ensureNamespace(namespace);
  const apiKey =
    getSecretValue(namespace, 'kubently-api-keys', 'keys')?.split('\n')[0] ?? genToken(24);
  applySecret(namespace, 'kubently-api-keys', { keys: apiKey });
  const redisPassword =
    getSecretValue(namespace, 'kubently-redis-password', 'password') ?? genToken(16);
  applySecret(namespace, 'kubently-redis-password', { password: redisPassword });
  applySecret(namespace, 'kubently-llm-secrets', { [envVar]: llmKey });
  spinner.succeed('Namespace and secrets ready');

  // 2. Helm install (waits for deployments). Reuse the existing executor token on
  // rerun — rotating it strands the running executor pod on the old value (401 loop).
  const executorToken =
    getSecretValue(namespace, 'kubently-executor-token', 'token') ?? genToken(32);
  spinner = ora('Installing Kubently via Helm (this can take a few minutes)').start();
  run(
    'helm',
    buildHelmArgs({
      namespace,
      clusterId,
      executorToken,
      provider: opts.provider,
      chartPath: opts.chart,
    })
  );
  spinner.succeed('Helm release installed');

  // 3. Port-forward + wait for API
  spinner = ora('Waiting for the Kubently API').start();
  const portForward = startPortForward(namespace);
  await waitFor(
    async () => (await axios.get(`${LOCAL_API_URL}/healthz`, { timeout: 2000 })).status === 200,
    'API health check'
  );
  spinner.succeed('API is up (port-forwarded to localhost:8080)');

  // 4. Wait for the executor to register (the chart's sync-executor-tokens init
  // container seeds executor:token:{clusterId} into Redis from the Helm secret).
  // Restart the executor first: pods don't roll on secret change, so a rerun or
  // recovery install would otherwise leave it authenticating with a stale token.
  spinner = ora('Waiting for the executor to connect').start();
  restartExecutor(namespace);
  const admin = new KubentlyAdminClient(LOCAL_API_URL, apiKey);
  await waitFor(async () => {
    const { clusters } = await admin.listClusters();
    return clusters.some((c) => c.id === clusterId && c.connected);
  }, `cluster '${clusterId}' to connect`);
  spinner.succeed(`Cluster '${clusterId}' connected`);

  // 5. Persist CLI config
  config.setApiUrl(LOCAL_API_URL);
  config.setApiKey(apiKey);
  config.save();
  console.log(chalk.green('\n✓ Kubently installed. CLI config saved to ~/.kubently/config.json'));
  console.log(chalk.gray('  The API is reachable while a port-forward is running; start one any time with:'));
  console.log(chalk.gray(`  kubectl -n ${namespace} port-forward svc/kubently-api 8080:8080\n`));

  // 6. Straight into the chat
  if (opts.chat) {
    await runDebugSession(LOCAL_API_URL, apiKey, clusterId);
  }
  portForward.kill();
}
