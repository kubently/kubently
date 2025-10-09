FROM python:3.13-alpine

# Install kubectl and uv dependencies
RUN apk add --no-cache curl bash gcc musl-dev && \
    ARCH=$(uname -m | sed 's/x86_64/amd64/' | sed 's/aarch64/arm64/') && \
    curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/${ARCH}/kubectl" && \
    chmod +x kubectl && \
    mv kubectl /usr/local/bin/ && \
    kubectl version --client && \
    apk del curl

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_SYSTEM_PYTHON=1
ENV UV_COMPILE_BYTECODE=1

# Set working directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml .
COPY README.md .

# Install only required dependencies for executor (base package)
RUN uv pip install --no-cache .

# Install SSE executor dependencies
RUN uv pip install --no-cache requests sseclient-py httpx

# Copy executor module
COPY kubently/ ./kubently/

# Create non-root user
RUN adduser -D -u 1000 kubently && \
    chown -R kubently:kubently /app
USER kubently

# Run the SSE executor based on EXECUTOR_TYPE environment variable
CMD ["sh", "-c", "python -u -m kubently.modules.executor.${EXECUTOR_TYPE:-sse}_executor"]