/**
 * OAuth Device Flow Client
 * 
 * Black Box Module: Handles OAuth device flow protocol
 * No UI, no config storage - single responsibility
 */

import axios, { AxiosError } from 'axios';

export interface DeviceCodeResponse {
  device_code: string;
  user_code: string;
  verification_uri: string;
  verification_uri_complete?: string;
  expires_in: number;
  interval?: number;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in?: number;
  refresh_token?: string;
  id_token?: string;
  scope?: string;
}

export interface OAuthConfig {
  issuer: string;
  clientId: string;
  deviceAuthEndpoint: string;
  tokenEndpoint: string;
  scopes: string[];
}

export class OAuthDeviceFlowClient {
  private readonly defaultInterval = 5; // seconds
  
  /**
   * Start device authorization flow
   * 
   * @param config - OAuth configuration
   * @returns Device code response
   */
  async startDeviceFlow(config: OAuthConfig): Promise<DeviceCodeResponse> {
    try {
      const response = await axios.post<DeviceCodeResponse>(
        config.deviceAuthEndpoint,
        new URLSearchParams({
          client_id: config.clientId,
          scope: config.scopes.join(' ')
        }),
        {
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded'
          }
        }
      );
      
      return response.data;
    } catch (error) {
      if (error instanceof AxiosError) {
        throw new Error(`Failed to start device flow: ${error.response?.data?.error_description || error.message}`);
      }
      throw error;
    }
  }
  
  /**
   * Poll for token after user authorization
   * 
   * @param config - OAuth configuration
   * @param deviceCode - Device code from startDeviceFlow
   * @param onPoll - Callback for each poll attempt
   * @param maxAttempts - Maximum poll attempts (default 60)
   * @returns Token response
   */
  async pollForToken(
    config: OAuthConfig,
    deviceCode: string,
    onPoll?: (attempt: number) => void,
    maxAttempts: number = 60
  ): Promise<TokenResponse> {
    const interval = this.defaultInterval * 1000; // Convert to milliseconds
    let attempt = 0;
    
    while (attempt < maxAttempts) {
      attempt++;
      
      if (onPoll) {
        onPoll(attempt);
      }
      
      try {
        const response = await axios.post<TokenResponse>(
          config.tokenEndpoint,
          new URLSearchParams({
            grant_type: 'urn:ietf:params:oauth:grant-type:device_code',
            device_code: deviceCode,
            client_id: config.clientId
          }),
          {
            headers: {
              'Content-Type': 'application/x-www-form-urlencoded'
            }
          }
        );
        
        return response.data;
      } catch (error) {
        if (error instanceof AxiosError) {
          const errorCode = error.response?.data?.error;
          
          if (errorCode === 'authorization_pending') {
            // User hasn't authorized yet, keep polling
            await this.sleep(interval);
            continue;
          } else if (errorCode === 'slow_down') {
            // Server wants us to slow down
            await this.sleep(interval * 2);
            continue;
          } else if (errorCode === 'access_denied') {
            throw new Error('User denied authorization');
          } else if (errorCode === 'expired_token') {
            throw new Error('Device code expired');
          } else {
            throw new Error(`Token exchange failed: ${error.response?.data?.error_description || errorCode || error.message}`);
          }
        }
        throw error;
      }
    }
    
    throw new Error('Polling timeout - user did not complete authorization');
  }
  
  /**
   * Validate JWT token (basic validation)
   * 
   * @param token - JWT token
   * @returns True if token appears valid
   */
  validateToken(token: string): boolean {
    // Basic JWT structure validation
    const parts = token.split('.');
    if (parts.length !== 3) {
      return false;
    }
    
    try {
      // Check if we can decode the payload
      const payload = JSON.parse(Buffer.from(parts[1], 'base64').toString());
      
      // Check expiration if present
      if (payload.exp) {
        const now = Math.floor(Date.now() / 1000);
        if (payload.exp < now) {
          return false; // Token expired
        }
      }
      
      return true;
    } catch {
      return false;
    }
  }
  
  private sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}