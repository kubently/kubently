import axios, { AxiosInstance } from 'axios';
import { v4 as uuidv4 } from 'uuid';
import { Config } from './config.js';
import https from 'https';

export interface A2AMessage {
  messageId: string;
  role: 'user' | 'assistant';
  parts: Array<{
    partId: string;
    text?: string;
    root?: { text: string };
  }>;
  contextId?: string;  // contextId belongs in the message
}

export interface A2ARequest {
  jsonrpc: '2.0';
  id: string;
  method: string;
  params: {
    message: A2AMessage;
    metadata?: Record<string, any>;  // A2A extension metadata
  };
}

export interface A2AResponse {
  jsonrpc: '2.0';
  id: string;
  result?: any;
  error?: {
    code: number;
    message: string;
    data?: any;
  };
}

export class KubentlyA2ASession {
  private client: AxiosInstance;
  private sessionId: string;
  private requestId: number;
  private config: Config;
  private clusterId?: string;

  constructor(apiUrl: string, apiKey?: string, clusterId?: string, insecure: boolean = false) {
    // Default to https:// if no protocol specified
    if (!apiUrl.startsWith('http://') && !apiUrl.startsWith('https://')) {
      apiUrl = 'https://' + apiUrl;
    }

    this.sessionId = uuidv4();
    this.requestId = 0;
    this.config = new Config();
    this.clusterId = clusterId;

    // Build headers based on authentication method
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'User-Agent': 'Kubently-CLI/2.0.0',
    };

    // If API key is explicitly provided, use it (legacy mode)
    if (apiKey) {
      headers['X-API-Key'] = apiKey;
    }

    // Configure axios with optional insecure mode for SSL
    const axiosConfig: any = {
      baseURL: apiUrl, // Keep trailing slash as-is
      timeout: 60000,
      headers,
    };

    // Disable SSL certificate verification in insecure mode (for testing only)
    if (insecure) {
      axiosConfig.httpsAgent = new https.Agent({
        rejectUnauthorized: false,
      });
    }

    this.client = axios.create(axiosConfig);
    
    // Add request interceptor to add auth headers dynamically
    this.client.interceptors.request.use(async (config) => {
      // If no API key was provided, use config-based auth
      if (!apiKey) {
        const authHeaders = await this.config.getAuthHeaders();
        Object.assign(config.headers, authHeaders);
      }
      return config;
    });
  }

  async sendMessage(message: string): Promise<{ success: boolean; output?: string; error?: string }> {
    this.requestId++;

    const request: A2ARequest = {
      jsonrpc: '2.0',
      id: String(this.requestId),
      method: 'message/stream',
      params: {
        message: {
          messageId: uuidv4(),
          role: 'user',
          parts: [{
            partId: uuidv4(),
            text: message
          }],
          contextId: this.sessionId,  // contextId must be inside message
        },
        // Pass cluster context via A2A metadata extension
        metadata: this.clusterId ? { clusterId: this.clusterId } : undefined,
      },
    };
    
    try {
      // Post directly to the base URL which should already include /a2a if needed
      const response = await this.client.post('', request, {
        responseType: 'text',
        transformResponse: [(data) => data], // Don't parse JSON automatically
      });

      // Parse SSE stream response
      const lines = String(response.data).split('\n');
      let lastMessage = '';
      let errorMessage = '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const dataStr = line.substring(6);
          if (dataStr === '[DONE]') continue;

          try {
            const data = JSON.parse(dataStr);

            // Check for errors
            if (data.error) {
              errorMessage = data.error.message || 'Unknown error';
              continue;
            }

            // Extract text from the response
            const text = this.extractResponseText(data);
            if (text) {
              lastMessage = text;
            }
          } catch (e) {
            // Skip unparseable lines
            continue;
          }
        }
      }

      if (errorMessage) {
        return {
          success: false,
          error: errorMessage,
        };
      }

      return {
        success: true,
        output: lastMessage || 'No response from server'
      };

    } catch (error) {
      if (axios.isAxiosError(error)) {
        if (error.response) {
          const status = error.response.status;

          // Provide specific error messages for authentication failures
          if (status === 401) {
            return {
              success: false,
              error: 'Authentication failed: Invalid or missing API key',
            };
          } else if (status === 403) {
            return {
              success: false,
              error: 'Access denied: API key does not have permission for this operation',
            };
          } else if (status === 404) {
            return {
              success: false,
              error: 'Endpoint not found: Check that the API URL is correct (should end with /a2a/)',
            };
          } else {
            return {
              success: false,
              error: `HTTP ${status}: ${error.response.statusText}`,
            };
          }
        } else if (error.request) {
          return {
            success: false,
            error: 'No response from server - check that the API URL is correct and the server is running',
          };
        }
      }
      return {
        success: false,
        error: error instanceof Error ? error.message : 'Unknown error',
      };
    }
  }

  private extractResponseText(response: any): string {
    if (!response.result) {
      return '';
    }

    const result = response.result;

    // Try to extract text from artifact (used in final responses)
    if (result.artifact && result.artifact.parts && Array.isArray(result.artifact.parts)) {
      for (const part of result.artifact.parts) {
        if (part.text) {
          return part.text;
        } else if (part.root && part.root.text) {
          return part.root.text;
        }
      }
    }

    // Try to extract text from artifacts (plural)
    if (result.artifacts && Array.isArray(result.artifacts) && result.artifacts.length > 0) {
      const artifact = result.artifacts[0];
      if (artifact.parts && Array.isArray(artifact.parts) && artifact.parts.length > 0) {
        const part = artifact.parts[0];
        if (part.text) {
          return part.text;
        } else if (part.root && part.root.text) {
          return part.root.text;
        }
      }
    }

    // Try status message
    if (result.status && result.status.message) {
      const msg = result.status.message;
      if (msg.parts && Array.isArray(msg.parts) && msg.parts.length > 0) {
        const part = msg.parts[0];
        if (part.text) {
          return part.text;
        } else if (part.root && part.root.text) {
          return part.root.text;
        }
      }
    }

    // Return empty string if can't extract specific text
    return '';
  }

  getSessionId(): string {
    return this.sessionId;
  }

  resetSession(): void {
    this.sessionId = uuidv4();
    this.requestId = 0;
  }
}