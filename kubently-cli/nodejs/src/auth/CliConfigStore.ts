/**
 * CLI Configuration Store
 * 
 * Black Box Module: Manages auth configuration persistence
 * No network, no UI, no OAuth logic - single responsibility
 */

import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

export interface AuthConfig {
  apiUrl?: string;
  apiKey?: string;
  token?: string;
  tokenType?: string;
  expiresAt?: number;
  refreshToken?: string;
  issuer?: string;
  clientId?: string;
  identity?: string;
  authMethod?: 'api_key' | 'oauth';
}

export class CliConfigStore {
  private readonly configDir: string;
  private readonly configFile: string;
  
  constructor(configDir?: string) {
    this.configDir = configDir || path.join(os.homedir(), '.kubently');
    this.configFile = path.join(this.configDir, 'auth.json');
  }
  
  /**
   * Load authentication configuration
   * 
   * @returns Stored config or empty object
   */
  load(): AuthConfig {
    try {
      if (fs.existsSync(this.configFile)) {
        const content = fs.readFileSync(this.configFile, 'utf-8');
        return JSON.parse(content);
      }
    } catch (error) {
      // Ignore errors, return empty config
    }
    
    return {};
  }
  
  /**
   * Save authentication configuration
   * 
   * @param config - Configuration to save
   */
  save(config: AuthConfig): void {
    try {
      // Ensure directory exists
      if (!fs.existsSync(this.configDir)) {
        fs.mkdirSync(this.configDir, { recursive: true, mode: 0o700 });
      }
      
      // Save config file with restricted permissions
      fs.writeFileSync(
        this.configFile,
        JSON.stringify(config, null, 2),
        { mode: 0o600 }
      );
    } catch (error) {
      throw new Error(`Failed to save configuration: ${error}`);
    }
  }
  
  /**
   * Update specific configuration fields
   * 
   * @param updates - Fields to update
   */
  update(updates: Partial<AuthConfig>): void {
    const current = this.load();
    const updated = { ...current, ...updates };
    this.save(updated);
  }
  
  /**
   * Clear authentication data
   */
  clearAuth(): void {
    const current = this.load();
    delete current.apiKey;
    delete current.token;
    delete current.tokenType;
    delete current.expiresAt;
    delete current.refreshToken;
    delete current.identity;
    delete current.authMethod;
    this.save(current);
  }
  
  /**
   * Get active token if valid
   * 
   * @returns Token or null if expired/missing
   */
  getValidToken(): string | null {
    const config = this.load();
    
    if (!config.token) {
      return null;
    }
    
    // Check expiration if set
    if (config.expiresAt) {
      const now = Math.floor(Date.now() / 1000);
      if (config.expiresAt < now) {
        return null; // Token expired
      }
    }
    
    return config.token;
  }
  
  /**
   * Get API key if configured
   * 
   * @returns API key or null
   */
  getApiKey(): string | null {
    const config = this.load();
    return config.apiKey || null;
  }
  
  /**
   * Store OAuth token
   * 
   * @param token - Access token
   * @param expiresIn - Expiration in seconds (optional)
   * @param refreshToken - Refresh token (optional)
   */
  storeToken(token: string, expiresIn?: number, refreshToken?: string): void {
    const updates: Partial<AuthConfig> = {
      token,
      tokenType: 'Bearer',
      authMethod: 'oauth',
      refreshToken
    };
    
    if (expiresIn) {
      updates.expiresAt = Math.floor(Date.now() / 1000) + expiresIn;
    }
    
    this.update(updates);
  }
  
  /**
   * Store API key
   * 
   * @param apiKey - API key
   */
  storeApiKey(apiKey: string): void {
    this.update({
      apiKey,
      authMethod: 'api_key'
    });
  }
}