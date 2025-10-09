/**
 * Login Command (Refactored for Black Box Design)
 * 
 * This is now a thin shell that delegates to LoginController
 * All implementation details are hidden in single-purpose modules
 */

import { Command } from 'commander';
import { LoginController } from '../auth/LoginController.js';

export function createLoginCommand(): Command {
  const login = new Command('login')
    .description('Authenticate with Kubently using OAuth or API key')
    .option('--api-url <url>', 'Kubently API URL for discovery')
    .option('--use-api-key <key>', 'Use API key instead of OAuth')
    .option('--issuer <url>', 'OIDC issuer URL (skips discovery)')
    .option('--client-id <id>', 'OAuth client ID (default: kubently-cli)')
    .option('--no-discovery', 'Skip authentication discovery')
    .option('--no-open', "Don't open browser automatically")
    .option('--silent', 'Minimal output')
    .action(async (options) => {
      // Create controller (dependencies are created internally)
      const controller = new LoginController();
      
      // Execute login flow
      const success = await controller.login(options);
      
      // Exit with appropriate code
      process.exit(success ? 0 : 1);
    });

  return login;
}