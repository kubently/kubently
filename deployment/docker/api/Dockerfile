# Build stage
FROM python:3.13-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_SYSTEM_PYTHON=1
ENV UV_COMPILE_BYTECODE=1

# Install build dependencies for compiled packages
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml .
COPY README.md .

# Install base dependencies
RUN uv pip install --no-cache .
# Install a2a dependencies (required for A2A support)
RUN uv pip install --no-cache ".[a2a]"

# Runtime stage
FROM python:3.13-slim

# Install uv in runtime stage
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_SYSTEM_PYTHON=1

# Copy Python packages from builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Set working directory
WORKDIR /app

# Copy application
COPY kubently/ ./kubently/
# Copy default prompts into image
COPY prompts/ /etc/kubently/prompts/

# Create non-root user
RUN useradd -m -u 1000 kubently && \
    chown -R kubently:kubently /app
USER kubently

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8080/health')" || exit 1

# Run application
EXPOSE 8080
CMD ["uvicorn", "kubently.main:app", "--host", "0.0.0.0", "--port", "8080"]