/**
 * Login Controller
 * 
 * Black Box Module: Orchestrates login flow
 * Coordinates other modules but contains no implementation details
 */

import { AuthDiscoveryClient } from './AuthDiscoveryClient.js';
import { OAuthDeviceFlowClient, OAuthConfig } from './OAuthDeviceFlowClient.js';
import { CliAuthUI } from './CliAuthUI.js';
import { CliConfigStore } from './CliConfigStore.js';

export interface LoginOptions {
  apiUrl?: string;
  useApiKey?: string;
  issuer?: string;
  clientId?: string;
  noDiscovery?: boolean;
  silent?: boolean;
  noOpen?: boolean;
}

export class LoginController {
  private readonly discoveryClient: AuthDiscoveryClient;
  private readonly oauthClient: OAuthDeviceFlowClient;
  private readonly ui: CliAuthUI;
  private readonly configStore: CliConfigStore;
  
  constructor(
    discoveryClient?: AuthDiscoveryClient,
    oauthClient?: OAuthDeviceFlowClient,
    ui?: CliAuthUI,
    configStore?: CliConfigStore
  ) {
    // Allow dependency injection for testing
    this.discoveryClient = discoveryClient || new AuthDiscoveryClient();
    this.oauthClient = oauthClient || new OAuthDeviceFlowClient();
    this.ui = ui || new CliAuthUI();
    this.configStore = configStore || new CliConfigStore();
  }
  
  /**
   * Execute login flow
   * 
   * @param options - Login options
   * @returns True if login successful
   */
  async login(options: LoginOptions): Promise<boolean> {
    try {
      this.ui.showWelcome();
      
      // API key authentication path
      if (options.useApiKey) {
        return await this.loginWithApiKey(options.useApiKey);
      }
      
      // OAuth authentication path
      return await this.loginWithOAuth(options);
    } catch (error) {
      this.ui.showError(error instanceof Error ? error.message : 'Unknown error');
      return false;
    }
  }
  
  /**
   * Login with API key
   * 
   * @param apiKey - API key (or prompt if not provided)
   * @returns True if successful
   */
  private async loginWithApiKey(apiKey?: string): Promise<boolean> {
    // Get API key from parameter or prompt
    const key = apiKey || await this.ui.promptForApiKey();
    
    if (!key) {
      this.ui.showError('API key is required');
      return false;
    }
    
    // Store API key
    this.configStore.storeApiKey(key);
    
    // Show success
    this.ui.showSuccess('API key configured', 'api_key');
    
    return true;
  }
  
  /**
   * Login with OAuth
   * 
   * @param options - Login options
   * @returns True if successful
   */
  private async loginWithOAuth(options: LoginOptions): Promise<boolean> {
    // Get OAuth configuration
    const oauthConfig = await this.getOAuthConfig(options);
    
    if (!oauthConfig) {
      return false;
    }
    
    // Start device flow
    const deviceResponse = await this.oauthClient.startDeviceFlow(oauthConfig);
    
    // Show instructions to user
    await this.ui.showDeviceCodeInstructions(
      deviceResponse.user_code,
      deviceResponse.verification_uri,
      deviceResponse.verification_uri_complete
    );
    
    // Start polling for token
    this.ui.startPolling();
    
    try {
      const tokenResponse = await this.oauthClient.pollForToken(
        oauthConfig,
        deviceResponse.device_code,
        (attempt) => this.ui.updatePolling(attempt)
      );
      
      this.ui.stopPolling(true);
      
      // Store token
      this.configStore.storeToken(
        tokenResponse.id_token || tokenResponse.access_token,
        tokenResponse.expires_in,
        tokenResponse.refresh_token
      );
      
      // Store OAuth config for future use
      this.configStore.update({
        issuer: oauthConfig.issuer,
        clientId: oauthConfig.clientId
      });
      
      // Extract identity from token (basic parsing)
      const identity = this.extractIdentity(tokenResponse.id_token || tokenResponse.access_token);
      
      this.ui.showSuccess(identity || 'OAuth user', 'oauth');
      
      return true;
    } catch (error) {
      this.ui.stopPolling(false);
      throw error;
    }
  }
  
  /**
   * Get OAuth configuration from options, discovery, or prompts
   * 
   * @param options - Login options
   * @returns OAuth config or null
   */
  private async getOAuthConfig(options: LoginOptions): Promise<OAuthConfig | null> {
    // 1. Try explicit options
    if (options.issuer && options.clientId) {
      return {
        issuer: options.issuer,
        clientId: options.clientId,
        deviceAuthEndpoint: `${options.issuer}/device/code`,
        tokenEndpoint: `${options.issuer}/token`,
        scopes: ['openid', 'email', 'profile']
      };
    }
    
    // 2. Try discovery (unless disabled)
    if (!options.noDiscovery) {
      const apiUrl = options.apiUrl || this.getApiUrl();
      
      if (apiUrl) {
        const discovery = await this.discoveryClient.discover(apiUrl);
        
        if (discovery) {
          const discoveredConfig = this.discoveryClient.getOAuthConfig(discovery);
          
          if (discoveredConfig) {
            return discoveredConfig;
          } else if (!this.discoveryClient.isOAuthAvailable(discovery)) {
            this.ui.showOAuthNotAvailable();
            return null;
          }
        }
      }
    }
    
    // 3. Try stored configuration
    const storedConfig = this.configStore.load();
    if (storedConfig.issuer && storedConfig.clientId) {
      return {
        issuer: storedConfig.issuer,
        clientId: storedConfig.clientId,
        deviceAuthEndpoint: `${storedConfig.issuer}/device/code`,
        tokenEndpoint: `${storedConfig.issuer}/token`,
        scopes: ['openid', 'email', 'profile']
      };
    }
    
    // 4. Prompt user for configuration
    const promptedConfig = await this.ui.promptForOidcConfig();
    
    if (!promptedConfig) {
      return null;
    }
    
    return {
      issuer: promptedConfig.issuer,
      clientId: promptedConfig.clientId,
      deviceAuthEndpoint: `${promptedConfig.issuer}/device/code`,
      tokenEndpoint: `${promptedConfig.issuer}/token`,
      scopes: ['openid', 'email', 'profile']
    };
  }
  
  /**
   * Get API URL from config or environment
   * 
   * @returns API URL or null
   */
  private getApiUrl(): string | null {
    const config = this.configStore.load();
    return config.apiUrl || process.env.KUBENTLY_API_URL || 'http://localhost:8080';
  }
  
  /**
   * Extract identity from JWT token (basic)
   * 
   * @param token - JWT token
   * @returns Identity or null
   */
  private extractIdentity(token: string): string | null {
    try {
      const parts = token.split('.');
      if (parts.length !== 3) return null;
      
      const payload = JSON.parse(Buffer.from(parts[1], 'base64').toString());
      return payload.email || payload.sub || null;
    } catch {
      return null;
    }
  }
}