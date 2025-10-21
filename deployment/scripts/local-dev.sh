#!/bin/bash
# Local development setup using Docker Compose

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
ACTION=${1:-up}
BUILD=${2:-false}

echo -e "${GREEN}Kubently Local Development Environment${NC}"

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$SCRIPT_DIR/../.."
COMPOSE_FILE="$PROJECT_ROOT/deployment/docker-compose.yaml"

cd "$PROJECT_ROOT"

# Check docker and docker-compose are available
if ! command -v docker &> /dev/null; then
    echo -e "${RED}docker could not be found${NC}"
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo -e "${YELLOW}docker-compose not found, trying docker compose...${NC}"
    DOCKER_COMPOSE="docker compose"
else
    DOCKER_COMPOSE="docker-compose"
fi

# Function to check if kubeconfig exists
check_kubeconfig() {
    if [ ! -f "$HOME/.kube/config" ]; then
        echo -e "${YELLOW}Warning: No kubeconfig found at ~/.kube/config${NC}"
        echo "The agent container will not be able to connect to any Kubernetes cluster."
        echo "To fix this, ensure you have a valid kubeconfig at ~/.kube/config"
        return 1
    fi
    return 0
}

case "$ACTION" in
    up|start)
        echo -e "${YELLOW}Starting Kubently services...${NC}"

        check_kubeconfig

        if [ "$BUILD" == "true" ]; then
            echo -e "${YELLOW}Building images...${NC}"
            ${DOCKER_COMPOSE} -f ${COMPOSE_FILE} build
        fi

        ${DOCKER_COMPOSE} -f ${COMPOSE_FILE} up -d

        echo -e "${GREEN}✓ Services started${NC}"
        echo ""

        # Initialize executor token registration
        echo -e "${YELLOW}Initializing executor authentication...${NC}"
        "${SCRIPT_DIR}/docker-compose-init.sh"
        ;;
        
    stop|down)
        echo -e "${YELLOW}Stopping Kubently services...${NC}"
        ${DOCKER_COMPOSE} -f ${COMPOSE_FILE} down
        echo -e "${GREEN}✓ Services stopped${NC}"
        ;;
        
    restart)
        echo -e "${YELLOW}Restarting Kubently services...${NC}"
        ${DOCKER_COMPOSE} -f ${COMPOSE_FILE} restart
        echo -e "${GREEN}✓ Services restarted${NC}"
        ;;
        
    logs)
        SERVICE=${BUILD:-}
        if [ -z "$SERVICE" ]; then
            ${DOCKER_COMPOSE} -f ${COMPOSE_FILE} logs -f
        else
            ${DOCKER_COMPOSE} -f ${COMPOSE_FILE} logs -f ${SERVICE}
        fi
        ;;
        
    status|ps)
        echo -e "${YELLOW}Service status:${NC}"
        ${DOCKER_COMPOSE} -f ${COMPOSE_FILE} ps
        ;;
        
    test)
        echo -e "${YELLOW}Running tests...${NC}"
        
        # Test Redis
        echo -n "Testing Redis connection... "
        docker exec kubently-redis redis-cli ping > /dev/null 2>&1
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✓${NC}"
        else
            echo -e "${RED}✗${NC}"
        fi
        
        # Test API health
        echo -n "Testing API health endpoint... "
        curl -f -s http://localhost:8080/health > /dev/null 2>&1
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✓${NC}"
        else
            echo -e "${RED}✗${NC}"
        fi
        
        # Check container status
        echo ""
        echo "Container status:"
        docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep kubently
        ;;
        
    clean)
        echo -e "${YELLOW}Cleaning up Kubently services and data...${NC}"
        ${DOCKER_COMPOSE} -f ${COMPOSE_FILE} down -v
        echo -e "${GREEN}✓ Cleanup complete${NC}"
        ;;
        
    build)
        echo -e "${YELLOW}Building Docker images...${NC}"
        ${DOCKER_COMPOSE} -f ${COMPOSE_FILE} build
        echo -e "${GREEN}✓ Build complete${NC}"
        ;;
        
    *)
        echo "Usage: $0 {up|stop|restart|logs|status|test|clean|build} [options]"
        echo ""
        echo "Commands:"
        echo "  up [build]     Start services (optionally rebuild images)"
        echo "  stop          Stop services"
        echo "  restart       Restart services"
        echo "  logs [service] View logs (optionally for specific service)"
        echo "  status        Show service status"
        echo "  test          Test service connectivity"
        echo "  clean         Stop services and remove volumes"
        echo "  build         Build Docker images"
        echo ""
        echo "Examples:"
        echo "  $0 up          # Start services"
        echo "  $0 up true     # Rebuild and start services"
        echo "  $0 logs api    # View API logs"
        echo "  $0 test        # Test service health"
        exit 1
        ;;
esac