import fs from 'fs';
import path from 'path';
import os from 'os';

export interface ClusterInfo {
  token: string;
  createdAt: string;
  namespace?: string;
}

export interface OAuthTokens {
  access_token?: string;
  refresh_token?: string;
  id_token?: string;
  expires_at?: number;
}

export interface ConfigData {
  apiUrl?: string;
  apiKey?: string;
  authMethod?: 'api_key' | 'oauth';
  oauthTokens?: OAuthTokens;
  oidcIssuer?: string;
  oidcClientId?: string;
  clusters: Record<string, ClusterInfo>;
  defaults: {
    namespace: string;
    image: string;
    timeout: number;
    ttl: number;
  };
}

export class Config {
  private configDir: string;
  private configFile: string;
  private config: ConfigData;
  private a2aPath?: string;

  constructor() {
    this.configDir = path.join(os.homedir(), '.kubently');
    this.configFile = path.join(this.configDir, 'config.json');
    
    // Create config directory if it doesn't exist
    if (!fs.existsSync(this.configDir)) {
      fs.mkdirSync(this.configDir, { mode: 0o700, recursive: true });
    }
    
    this.config = this.loadConfig();
  }

  private loadConfig(): ConfigData {
    if (fs.existsSync(this.configFile)) {
      try {
        const data = fs.readFileSync(this.configFile, 'utf-8');
        return JSON.parse(data);
      } catch (error) {
        // If config is corrupted, backup and create new
        const backupFile = this.configFile + '.bak';
        fs.renameSync(this.configFile, backupFile);
        console.warn(`Config file corrupted. Backed up to ${backupFile}`);
        return this.getDefaultConfig();
      }
    }
    return this.getDefaultConfig();
  }

  private getDefaultConfig(): ConfigData {
    return {
      apiUrl: undefined,
      apiKey: undefined,
      authMethod: 'api_key',
      oauthTokens: undefined,
      oidcIssuer: undefined,
      oidcClientId: undefined,
      clusters: {},
      defaults: {
        namespace: 'kubently',
        image: 'kubently/agent:latest',
        timeout: 30,
        ttl: 300,
      },
    };
  }

  save(): void {
    // Ensure directory exists
    if (!fs.existsSync(this.configDir)) {
      fs.mkdirSync(this.configDir, { mode: 0o700, recursive: true });
    }
    
    // Write config with restrictive permissions
    fs.writeFileSync(this.configFile, JSON.stringify(this.config, null, 2));
    fs.chmodSync(this.configFile, 0o600);
  }

  getApiUrl(): string | undefined {
    // Environment variable takes precedence
    return process.env.KUBENTLY_API_URL || this.config.apiUrl;
  }

  getApiKey(): string | undefined {
    // Environment variable takes precedence
    return process.env.KUBENTLY_API_KEY || this.config.apiKey;
  }

  setApiUrl(url: string): void {
    this.config.apiUrl = url;
  }

  setApiKey(key: string): void {
    this.config.apiKey = key;
  }

  addCluster(clusterId: string, info: ClusterInfo): void {
    this.config.clusters[clusterId] = info;
    this.save();
  }

  removeCluster(clusterId: string): boolean {
    if (this.config.clusters[clusterId]) {
      delete this.config.clusters[clusterId];
      this.save();
      return true;
    }
    return false;
  }

  getCluster(clusterId: string): ClusterInfo | undefined {
    return this.config.clusters[clusterId];
  }

  listClusters(): Record<string, ClusterInfo> {
    return this.config.clusters;
  }

  getDefault<K extends keyof ConfigData['defaults']>(
    key: K
  ): ConfigData['defaults'][K] {
    return this.config.defaults[key];
  }

  setDefault<K extends keyof ConfigData['defaults']>(
    key: K,
    value: ConfigData['defaults'][K]
  ): void {
    this.config.defaults[key] = value;
    this.save();
  }

  validate(): boolean {
    return !!(this.getApiUrl() && this.getApiKey());
  }

  getA2aPath(): string | undefined {
    return this.a2aPath || process.env.KUBENTLY_A2A_PATH;
  }

  setA2aPath(path: string): void {
    this.a2aPath = path;
  }

  clear(): void {
    this.config = this.getDefaultConfig();
    this.save();
  }

  // OAuth-related methods
  getAuthMethod(): 'api_key' | 'oauth' {
    return this.config.authMethod || 'api_key';
  }

  setAuthMethod(method: 'api_key' | 'oauth'): void {
    this.config.authMethod = method;
  }

  getOAuthTokens(): OAuthTokens | undefined {
    return this.config.oauthTokens;
  }

  setOAuthTokens(tokens: OAuthTokens): void {
    this.config.oauthTokens = tokens;
  }

  getOIDCIssuer(): string | undefined {
    return process.env.OIDC_ISSUER || this.config.oidcIssuer;
  }

  setOIDCIssuer(issuer: string): void {
    this.config.oidcIssuer = issuer;
  }

  getOIDCClientId(): string | undefined {
    return process.env.OIDC_CLIENT_ID || this.config.oidcClientId;
  }

  setOIDCClientId(clientId: string): void {
    this.config.oidcClientId = clientId;
  }

  isTokenExpired(): boolean {
    const tokens = this.getOAuthTokens();
    if (!tokens || !tokens.expires_at) {
      return true;
    }
    return Date.now() >= tokens.expires_at;
  }

  async getAuthHeaders(): Promise<Record<string, string>> {
    const authMethod = this.getAuthMethod();
    
    if (authMethod === 'oauth') {
      const tokens = this.getOAuthTokens();
      if (!tokens || !tokens.access_token) {
        throw new Error('No OAuth tokens available. Please run: kubently login');
      }
      
      // Check if token is expired
      if (this.isTokenExpired()) {
        // TODO: Implement token refresh
        throw new Error('OAuth token expired. Please run: kubently login');
      }
      
      return {
        'Authorization': `Bearer ${tokens.access_token}`
      };
    } else {
      // API key authentication
      const apiKey = this.getApiKey();
      if (!apiKey) {
        throw new Error('No API key configured. Please run: kubently configure');
      }
      
      return {
        'X-API-Key': apiKey
      };
    }
  }
}