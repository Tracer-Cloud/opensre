# Production Dockerfile for OpenSRE
# Builds a container image that runs the LangGraph API server in production.
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

FROM langchain/langgraph-api:3.11

# Add the application source for installation and runtime loading.
ADD . /deps/agent

# Install the package and its dependencies into the API image.
RUN PYTHONDONTWRITEBYTECODE=1 \
    pip install --no-cache-dir -c /api/constraints.txt /deps/agent

# Configure the LangGraph API server to load the app graph.
#
# Intentionally omit custom auth here: self-hosted LangGraph deployments on
# Railway do not support the enterprise-only custom auth mode used by the
# Tracer-hosted path.
ENV LANGSERVE_GRAPHS='{"agent":"/deps/agent/app/graph_pipeline.py:build_graph"}'

WORKDIR /deps/agent

# Expose the LangGraph API port
EXPOSE 2024

# Health check - the LangGraph API server exposes /ok.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:2024/ok', timeout=5)" || exit 1
