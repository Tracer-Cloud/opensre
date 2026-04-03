# Production Dockerfile for OpenSRE
# Builds a container image that runs the LangGraph-based OpenSRE agent server.
#
# Usage:
#   docker build -t opensre:latest .
#   docker run -p 2024:2024 --env-file .env opensre:latest
#
# Health check:
#   curl http://localhost:2024/ok
#
# The server exposes the LangGraph API on port 2024 with endpoints:
#   - GET /ok          - Health check endpoint
#   - POST /threads    - Create conversation threads
#   - POST /threads/{id}/runs - Execute agent runs
#   - GET  /threads/{id}/state  - Get run state and results

FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install system dependencies for build
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first for layer caching
COPY pyproject.toml ./
COPY app/ ./app/
COPY langgraph.json ./

# Install Python dependencies and the package
# Install both production dependencies and langgraph-cli for the server
RUN pip install -e "." langgraph-cli

# Create non-root user for security
RUN useradd -m -u 1000 opensre && chown -R opensre:opensre /app
USER opensre

# Expose the LangGraph API port
EXPOSE 2024

# Health check - LangGraph server exposes /ok endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:2024/ok', timeout=5)" || exit 1

# Start the LangGraph server
# Uses the configuration from langgraph.json
# Note: 'langgraph dev' is the standard command to run the LangGraph API server
CMD ["langgraph", "dev", "--host", "0.0.0.0", "--no-browser"]
