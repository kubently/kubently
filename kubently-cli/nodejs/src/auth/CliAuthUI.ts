/**
 * CLI Authentication UI
 * 
 * Black Box Module: Handles all user interaction
 * No network, no config, no business logic - single responsibility
 */

import chalk from 'chalk';
import ora, { Ora } from 'ora';
import open from 'open';
import prompts from 'prompts';

export interface AuthUIOptions {
  silent?: boolean;
  noOpen?: boolean;
}

export class CliAuthUI {
  private spinner: Ora | null = null;
  private options: AuthUIOptions;
  
  constructor(options: AuthUIOptions = {}) {
    this.options = options;
  }
  
  /**
   * Show welcome message
   */
  showWelcome(): void {
    if (this.options.silent) return;
    
    console.log(chalk.cyan('\n🔐 Kubently Authentication\n'));
  }
  
  /**
   * Show OAuth not available message
   */
  showOAuthNotAvailable(): void {
    console.log(chalk.yellow('ℹ️  OAuth is not enabled for this Kubently instance'));
    console.log(chalk.gray('   Please use API key authentication instead:'));
    console.log(chalk.gray('   kubently login --use-api-key <your-api-key>'));
  }
  
  /**
   * Show device code instructions
   * 
   * @param userCode - User code to enter
   * @param verificationUrl - URL to visit
   * @param completeUrl - Complete URL with code (optional)
   */
  async showDeviceCodeInstructions(
    userCode: string,
    verificationUrl: string,
    completeUrl?: string
  ): Promise<void> {
    console.log(chalk.green('\n📱 Device Authorization Required\n'));
    console.log('Please complete authentication in your browser:');
    console.log('');
    console.log(chalk.cyan('  1. Visit: ') + chalk.underline(verificationUrl));
    console.log(chalk.cyan('  2. Enter code: ') + chalk.bold.yellow(userCode));
    console.log('');
    
    // Try to open browser automatically
    if (!this.options.noOpen && completeUrl) {
      try {
        await open(completeUrl);
        console.log(chalk.gray('  ✓ Browser opened automatically'));
      } catch {
        // Silent fail - user can open manually
      }
    }
  }
  
  /**
   * Start polling spinner
   */
  startPolling(): void {
    if (this.options.silent) return;
    
    this.spinner = ora({
      text: 'Waiting for authorization...',
      spinner: 'dots'
    }).start();
  }
  
  /**
   * Update polling status
   * 
   * @param attempt - Current attempt number
   */
  updatePolling(attempt: number): void {
    if (this.spinner) {
      this.spinner.text = `Waiting for authorization... (attempt ${attempt})`;
    }
  }
  
  /**
   * Stop polling spinner
   * 
   * @param success - Whether polling succeeded
   */
  stopPolling(success: boolean): void {
    if (this.spinner) {
      if (success) {
        this.spinner.succeed('Authorization successful!');
      } else {
        this.spinner.fail('Authorization failed');
      }
      this.spinner = null;
    }
  }
  
  /**
   * Show success message
   * 
   * @param identity - User identity
   * @param method - Auth method used
   */
  showSuccess(identity: string, method: string): void {
    console.log('');
    console.log(chalk.green('✅ Authentication successful!'));
    console.log(chalk.gray(`   Identity: ${identity}`));
    console.log(chalk.gray(`   Method: ${method}`));
  }
  
  /**
   * Show error message
   * 
   * @param message - Error message
   */
  showError(message: string): void {
    console.log('');
    console.log(chalk.red('❌ Authentication failed:'));
    console.log(chalk.red(`   ${message}`));
  }
  
  /**
   * Prompt for API key
   * 
   * @returns API key or null if cancelled
   */
  async promptForApiKey(): Promise<string | null> {
    const response = await prompts({
      type: 'password',
      name: 'apiKey',
      message: 'Enter your API key:',
      validate: (value: string) => value.length > 0 || 'API key is required'
    });
    
    return response.apiKey || null;
  }
  
  /**
   * Prompt for OIDC configuration
   * 
   * @returns OIDC config or null if cancelled
   */
  async promptForOidcConfig(): Promise<{
    issuer: string;
    clientId: string;
  } | null> {
    const response = await prompts([
      {
        type: 'text',
        name: 'issuer',
        message: 'OIDC Issuer URL:',
        validate: (value: string) => {
          try {
            new URL(value);
            return true;
          } catch {
            return 'Please enter a valid URL';
          }
        }
      },
      {
        type: 'text',
        name: 'clientId',
        message: 'Client ID:',
        initial: 'kubently-cli',
        validate: (value: string) => value.length > 0 || 'Client ID is required'
      }
    ]);
    
    if (!response.issuer || !response.clientId) {
      return null;
    }
    
    return {
      issuer: response.issuer,
      clientId: response.clientId
    };
  }
  
  /**
   * Confirm action
   * 
   * @param message - Confirmation message
   * @returns True if confirmed
   */
  async confirm(message: string): Promise<boolean> {
    const response = await prompts({
      type: 'confirm',
      name: 'confirmed',
      message,
      initial: true
    });
    
    return response.confirmed === true;
  }
}