.PHONY: help build deploy-kind deploy-k8s clean test lint

IMAGE_NAME?=kubently/api
IMAGE_TAG?=dev

# Default target - show help
help:
	@echo "Kubently Makefile Commands:"
	@echo ""
	@echo "Development:"
	@echo "  make install         - Install base dependencies with uv"
	@echo "  make install-dev     - Install dev and test dependencies"
	@echo "  make install-a2a     - Install A2A dependencies"
	@echo "  make install-all     - Install all dependencies"
	@echo "  make test           - Run tests"
	@echo "  make lint           - Run linters"
	@echo "  make run-local      - Run API server locally"
	@echo "  make run-a2a        - Run A2A server locally"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-build   - Build Docker image"
	@echo "  make docker-run     - Run with docker-compose"
	@echo "  make docker-stop    - Stop docker-compose services"
	@echo ""
	@echo "Kubernetes (Kind):"
	@echo "  make kind-create    - Create Kind cluster"
	@echo "  make kind-load      - Load Docker image to Kind"
	@echo "  make kind-deploy    - Deploy to Kind with .env file"
	@echo "  make kind-delete    - Delete Kind cluster"
	@echo ""
	@echo "Kubernetes (Production):"
	@echo "  make k8s-deploy     - Deploy to Kubernetes"
	@echo "  make k8s-secrets    - Create LLM API secrets"
	@echo "  make k8s-logs       - Show API pod logs"
	@echo "  make k8s-status     - Show deployment status"
	@echo ""
	@echo "CLI:"
	@echo "  make cli-build      - Build CLI package"
	@echo "  make cli-install    - Install CLI locally (editable)"
	@echo "  make cli-dist       - Build distribution packages"
	@echo "  make cli-test       - Test CLI installation"
	@echo ""

# Development targets
install:
	uv pip install -e .

install-dev:
	uv pip install -e ".[dev,test]"

install-a2a:
	uv pip install -e ".[a2a]"

install-all:
	uv pip install -e ".[dev,test,docs,a2a]"

test:
	cd kubently && python -m pytest tests/

lint:
	cd kubently && python -m ruff check .
	cd kubently && python -m ruff format . --check

run-local:
	cd kubently && python -m api.main

run-a2a:
	cd kubently/modules/a2a/protocol_bindings/a2a_server && python -m a2a server

# Docker targets
docker-build:
	docker build $(if $(NO_CACHE),--no-cache,) -f deployment/docker/api/Dockerfile -t $(IMAGE_NAME):$(IMAGE_TAG) -t $(IMAGE_NAME):latest .

docker-run:
	docker-compose -f deployment/docker-compose.yaml up -d

docker-stop:
	docker-compose -f deployment/docker-compose.yaml down

docker-logs:
	docker-compose -f deployment/docker-compose.yaml logs -f

# Kind (Kubernetes in Docker) targets
# WARNING: These commands use the persistent cluster (currently 'kubently', transitioning to 'kind-kubently')
# DO NOT delete this cluster without explicit permission!
kind-create:
	@echo "Creating persistent Kind cluster..."
	@echo "WARNING: This cluster should be persistent for development."
	@if kind get clusters | grep -q "^kubently$$"; then \
		echo "Using existing cluster 'kubently'"; \
	elif kind get clusters | grep -q "^kind-kubently$$"; then \
		echo "Using existing cluster 'kind-kubently'"; \
	else \
		echo "Creating new cluster 'kind-kubently'"; \
		kind create cluster --name kind-kubently --config deployment/kind-config.yaml; \
	fi

kind-load: docker-build
	@if kind get clusters | grep -q "^kubently$$"; then \
	kind load docker-image $(IMAGE_NAME):$(IMAGE_TAG) --name kubently; \
	kind load docker-image $(IMAGE_NAME):latest --name kubently; \
	else \
	kind load docker-image $(IMAGE_NAME):$(IMAGE_TAG) --name kind-kubently; \
	kind load docker-image $(IMAGE_NAME):latest --name kind-kubently; \
	fi

kind-deploy:
	@echo "WARNING: This target is deprecated. Use './deploy-test.sh' instead."
	@echo "The deploy-test.sh script uses Helm and handles all configuration properly."
	@false

kind-deploy-secrets:
	@echo "WARNING: This target is deprecated. Secrets are managed via Helm values."
	@echo "See deployment/helm/test-values.yaml for secret configuration."
	@false

kind-delete:
	@echo "ERROR: This would delete the persistent development cluster!"
	@echo "This cluster may contain important data and deployments."
	@echo "Current cluster: $(shell kind get clusters 2>/dev/null | head -1)"
	@echo "If you really need to delete it, ask the user explicitly."
	@echo "Manual command: kind delete cluster --name <cluster-name>"
	@false

kind-logs:
	kubectl logs -n kubently -l app=kubently-api --tail=100 -f

# Generate raw Kubernetes manifests from Helm chart
helm-template:
	@echo "Generating Kubernetes manifests from Helm chart..."
	@mkdir -p generated-manifests
	helm template kubently ./deployment/helm/kubently \
		-f deployment/helm/test-values.yaml \
		--namespace kubently \
		> generated-manifests/kubently-manifests.yaml
	@echo "Manifests generated at: generated-manifests/kubently-manifests.yaml"

# Kubernetes production targets (deprecated - use helm or helm-template)
k8s-deploy:
	@echo "WARNING: This target is deprecated. Use 'make helm-deploy' or './deploy-test.sh' instead."
	@echo "For raw manifests, use 'make helm-template' then 'kubectl apply -f generated-manifests/'"
	@false

k8s-secrets:
	@echo "WARNING: This target is deprecated. Secrets are managed via Helm values or kubectl."
	@echo "See deployment/helm/test-values.yaml for secret configuration."
	@false

k8s-logs:
	kubectl logs -n kubently -l app=kubently-api --tail=100 -f

k8s-status:
	kubectl get all -n kubently

k8s-port-forward:
	kubectl port-forward -n kubently svc/kubently-api 8080:8080 &
	@echo "API available at http://localhost:8080"

k8s-clean:
	kubectl delete namespace kubently

# Development with .env file
dev-env:
	@if [ ! -f .env ]; then \
		echo "Creating .env file from template..."; \
		cp .env.example .env; \
		echo "Please edit .env file with your configuration"; \
	else \
		echo ".env file already exists"; \
	fi

# Testing A2A locally with Kind
test-a2a-local: kind-deploy
	@echo "Setting up A2A testing environment..."
	kubectl port-forward -n kubently svc/kubently-api 8080:8080 &
	@echo ""
	@echo "A2A server is now available at localhost:8080/a2a/"
	@echo "Test with: curl -X POST http://localhost:8080/a2a/ -H 'Content-Type: application/json' -d '{...}'"

# CLI targets
cli-build:
	@echo "Building kubently-cli package..."
	cd kubently-cli && python -m pip install --upgrade build
	cd kubently-cli && python -m build

cli-install:
	@echo "Installing kubently-cli in editable mode..."
	cd kubently-cli && pip install -e .

cli-dist: cli-build
	@echo "CLI packages built in kubently-cli/dist/"
	@ls -la kubently-cli/dist/

cli-test: cli-install
	@echo "Testing kubently CLI..."
	which kubently
	kubently --help

cli-clean:
	rm -rf kubently-cli/dist kubently-cli/build kubently-cli/*.egg-info

# Clean everything
clean: cli-clean
	docker-compose -f deployment/docker-compose.yaml down -v
	docker rmi kubently/api:latest || true
	kubectl delete namespace kubently || true