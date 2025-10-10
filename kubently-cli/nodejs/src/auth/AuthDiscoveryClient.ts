/**
 * Authentication Discovery Client
 * 
 * Black Box Module: Only fetches discovery JSON from API
 * No UI, no config, no OAuth flow - single responsibility
 */

import axios, { AxiosError } from 'axios';

export interface AuthDiscoveryResponse {
  authentication_methods: string[];
  api_key?: {
    header: string;
    description: string;
  };
  oauth?: {
    enabled: boolean;
    issuer?: string;
    client_id?: string;
    device_authorization_endpoint?: string;
    token_endpoint?: string;
    grant_types?: string[];
    response_types?: string[];
    scopes?: string[];
    message?: string;
  };
}

export class AuthDiscoveryClient {
  /**
   * Fetch authentication discovery from API
   * 
   * @param apiUrl - Base API URL
   * @returns Discovery response or null if not available
   */
  async discover(apiUrl: string): Promise<AuthDiscoveryResponse | null> {
    try {
      const discoveryUrl = `${apiUrl}/.well-known/kubently-auth`;
      const response = await axios.get<AuthDiscoveryResponse>(discoveryUrl, {
        timeout: 5000,
        validateStatus: (status) => status === 200
      });
      
      return response.data;
    } catch (error) {
      // Discovery is optional - don't throw, just return null
      if (error instanceof AxiosError && error.response?.status === 404) {
        // Discovery not implemented
        return null;
      }
      
      // Network or other errors
      return null;
    }
  }
  
  /**
   * Check if OAuth is available based on discovery
   * 
   * @param discovery - Discovery response
   * @returns True if OAuth is enabled and configured
   */
  isOAuthAvailable(discovery: AuthDiscoveryResponse | null): boolean {
    if (!discovery) return false;
    return discovery.oauth?.enabled === true && !!discovery.oauth.issuer;
  }
  
  /**
   * Extract OAuth configuration from discovery
   * 
   * @param discovery - Discovery response
   * @returns OAuth config or null
   */
  getOAuthConfig(discovery: AuthDiscoveryResponse | null): {
    issuer: string;
    clientId: string;
    deviceAuthEndpoint: string;
    tokenEndpoint: string;
    scopes: string[];
  } | null {
    if (!this.isOAuthAvailable(discovery)) {
      return null;
    }
    
    const oauth = discovery!.oauth!;
    return {
      issuer: oauth.issuer!,
      clientId: oauth.client_id || 'kubently-cli',
      deviceAuthEndpoint: oauth.device_authorization_endpoint || `${oauth.issuer}/device/code`,
      tokenEndpoint: oauth.token_endpoint || `${oauth.issuer}/token`,
      scopes: oauth.scopes || ['openid', 'email', 'profile']
    };
  }
}