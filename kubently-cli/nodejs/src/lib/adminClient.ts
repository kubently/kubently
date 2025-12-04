import axios, { AxiosInstance, AxiosError } from 'axios';

export interface AgentToken {
  token: string;
  clusterId: string;
  createdAt: string;
}

export interface ExecutorCapabilities {
  cluster_id: string;
  mode: string;
  allowed_verbs: string[];
  restricted_resources: string[];
  allowed_flags: string[];
  executor_version?: string;
  executor_pod?: string;
  reported_at?: string;
  expires_at?: string;
  features: Record<string, boolean>;
}

export interface ClusterStatus {
  id: string;
  status: string;
  connected: boolean;
  lastSeen?: string;
  version?: string;
  kubernetesVersion?: string;
  mode?: string;  // Security mode (readOnly, extendedReadOnly, fullAccess)
  capabilities?: ExecutorCapabilities;  // Full capability details
}

export interface ClusterListItem {
  id: string;
  connected: boolean;
  lastSeen?: string;
}

export interface CommandResult {
  success: boolean;
  output?: string;
  error?: string;
}

export class KubentlyAdminClient {
  private client: AxiosInstance;
  private apiUrl: string;

  constructor(apiUrl: string, apiKey: string, timeout = 30000) {
    // Add http:// prefix if no protocol specified
    if (!apiUrl.startsWith('http://') && !apiUrl.startsWith('https://')) {
      apiUrl = 'http://' + apiUrl;
    }
    
    // Remove trailing slash AND any /a2a path if present
    this.apiUrl = apiUrl.replace(/\/$/, '').replace(/\/a2a$/, '');
    
    if (process.env.DEBUG === 'true') {
      console.log('DEBUG: AdminClient apiUrl:', this.apiUrl);
    }
    
    this.client = axios.create({
      baseURL: this.apiUrl,
      timeout,
      headers: {
        'X-API-Key': apiKey,
        'User-Agent': 'Kubently-CLI/2.0.0',
        'Content-Type': 'application/json',
      },
    });

    // Add response interceptor for error handling
    this.client.interceptors.response.use(
      response => response,
      (error: AxiosError) => {
        if (error.response) {
          const message = (error.response.data as any)?.message || error.message;
          throw new Error(`API Error (${error.response.status}): ${message}`);
        } else if (error.request) {
          throw new Error(`Cannot connect to API at ${this.apiUrl}`);
        }
        throw error;
      }
    );
  }

  async createAgentToken(clusterId: string, customToken?: string): Promise<AgentToken> {
    const body = customToken ? { token: customToken } : undefined;
    const response = await this.client.post(`/admin/agents/${clusterId}/token`, body);
    return response.data;
  }

  async listClusters(): Promise<{ clusters: ClusterListItem[] }> {
    if (process.env.DEBUG === 'true') {
      console.log('DEBUG: Calling GET', this.apiUrl + '/admin/agents');
    }
    const response = await this.client.get('/admin/agents');
    return response.data;
  }

  async getClusterStatus(clusterId: string): Promise<ClusterStatus> {
    const response = await this.client.get(`/admin/agents/${clusterId}/status`);
    return response.data;
  }

  async revokeAgentToken(clusterId: string): Promise<void> {
    await this.client.delete(`/admin/agents/${clusterId}/token`);
  }

  async executeSingleCommand(clusterId: string, command: string): Promise<CommandResult> {
    const args = command.split(' ');
    const response = await this.client.post('/debug/execute', {
      cluster_id: clusterId,
      args,
    });
    return response.data;
  }

  async testConnection(): Promise<boolean> {
    try {
      const response = await this.client.get('/health', { timeout: 5000 });
      return response.status === 200;
    } catch {
      return false;
    }
  }
}