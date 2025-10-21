# Kubently Deployment

This directory contains all deployment configurations for the Kubently system, including Docker images, Kubernetes manifests, Helm charts, and deployment scripts.

## Directory Structure

```
deployment/
├── docker/              # Docker configurations
│   ├── api/            # API Dockerfile and requirements
│   └── agent/          # Agent Dockerfile and requirements
├── kubernetes/         # Raw Kubernetes manifests
│   ├── namespace.yaml
│   ├── redis/         # Redis deployment and service
│   ├── api/           # API deployment, service, ingress
│   └── agent/         # Agent deployment and RBAC
├── helm/              # Helm chart
│   └── kubently/
│       ├── Chart.yaml
│       ├── values.yaml
│       └── templates/
├── scripts/           # Deployment automation scripts
└── docker-compose.yaml # Local development setup
```

## Quick Start

### Local Development with Docker Compose

Start the entire stack locally using Docker Compose for faster iteration:

```bash
# Configure environment (first time only)
cd deployment/
cp .env.example .env
# Edit .env and add your LLM API key

# Start services
./scripts/local-dev.sh up

# Or rebuild and start
./scripts/local-dev.sh up true

# View logs
./scripts/local-dev.sh logs

# Run tests
./scripts/local-dev.sh test

# Stop services
./scripts/local-dev.sh stop
```

**📖 See [DOCKER_COMPOSE.md](DOCKER_COMPOSE.md) for complete Docker Compose guide**

### Kubernetes Deployment

Deploy to Kubernetes using raw manifests:

```bash
# Create secrets
./deployment/scripts/create-secrets.sh kubently staging

# Deploy all components
./deployment/scripts/deploy.sh staging kubently

# Test deployment
./deployment/scripts/test-deployment.sh kubently
```

### Helm Deployment

Deploy using Helm chart:

```bash
# Install with default values
./deployment/scripts/helm-deploy.sh kubently kubently

# Install with custom values
helm install kubently ./deployment/helm/kubently \
  --namespace kubently \
  --create-namespace \
  --set api.apiKeys[0]=mykey1 \
  --set api.apiKeys[1]=mykey2 \
  --set ingress.enabled=true \
  --set ingress.hosts[0].host=api.example.com
```

## Building Docker Images

```bash
# Build images with latest tag
./deployment/scripts/build.sh

# Build with specific version
./deployment/scripts/build.sh v1.0.0

# Build and push to registry
./deployment/scripts/build.sh v1.0.0 myregistry true
```

## Configuration

### Environment Variables

#### API Configuration

- `REDIS_URL`: Redis connection URL (default: redis://redis:6379)
- `API_KEYS`: Comma-separated list of API keys for authentication
- `LOG_LEVEL`: Logging level (DEBUG, INFO, WARNING, ERROR)
- `MAX_COMMANDS_PER_FETCH`: Maximum commands to fetch per request
- `COMMAND_TIMEOUT`: Command execution timeout in seconds
- `SESSION_TTL`: Session time-to-live in seconds

#### Agent Configuration

- `KUBENTLY_API_URL`: URL of the Kubently API
- `CLUSTER_ID`: Unique identifier for the cluster
- `KUBENTLY_TOKEN`: Authentication token for the agent
- `LOG_LEVEL`: Logging level (DEBUG, INFO, WARNING, ERROR)
- `POLL_INTERVAL`: Interval between API polls in seconds

### Helm Values

Key values to customize:

```yaml
# API settings
api:
  replicaCount: 2
  image:
    repository: kubently/api
    tag: latest
  apiKeys:
    - "key1"
    - "key2"

# Agent settings
agent:
  enabled: true
  clusterId: "my-cluster"
  token: "agent-token"

# Redis settings
redis:
  enabled: true
  persistence:
    enabled: true
    size: 1Gi

# Ingress settings
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: api.kubently.com
      paths:
        - path: /
          pathType: Prefix
```

## Production Deployment Checklist

- [ ] Use external Redis cluster (AWS ElastiCache, Redis Cloud)
- [ ] Configure PersistentVolume for Redis if self-hosted
- [ ] Set up TLS certificates (cert-manager)
- [ ] Configure appropriate resource limits
- [ ] Set up monitoring (Prometheus, Grafana)
- [ ] Configure log aggregation (Fluentd, Loki)
- [ ] Set up backup strategy for Redis
- [ ] Implement secret rotation
- [ ] Configure network policies
- [ ] Enable autoscaling
- [ ] Set up pod disruption budgets
- [ ] Configure health checks and readiness probes
- [ ] Set up alerting rules

## Security Considerations

1. **RBAC**: Agent runs with minimal read-only permissions
2. **Non-root containers**: All containers run as non-root users
3. **Network policies**: Restrict traffic between components
4. **Secret management**: Use Kubernetes secrets or external secret managers
5. **TLS**: Enable TLS for all external endpoints
6. **API authentication**: Use strong API keys and rotate regularly

## Troubleshooting

### Check component status

```bash
kubectl get pods -n kubently
kubectl get deployments -n kubently
kubectl get services -n kubently
```

### View logs

```bash
# API logs
kubectl logs -n kubently deployment/kubently-api

# Agent logs
kubectl logs -n kubently deployment/kubently-agent

# Redis logs
kubectl logs -n kubently deployment/redis
```

### Test connectivity

```bash
# Port forward API
kubectl port-forward -n kubently svc/kubently-api 8080:80

# Test health endpoint
curl http://localhost:8080/health
```

### Common Issues

1. **Agent can't connect to API**: Check service DNS and network policies
2. **API can't connect to Redis**: Verify Redis service is running
3. **Agent permission denied**: Check RBAC configuration
4. **Ingress not working**: Verify ingress controller and TLS certificates

## Monitoring

The system exposes metrics that can be scraped by Prometheus:

- Command queue depth
- Command execution latency
- Success/failure rates
- API request latency
- Redis connection pool stats

## Support

For issues or questions, please refer to the main project documentation or create an issue in the repository.
