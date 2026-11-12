# AgentForge API - production image. Build from the repo root:
#   docker build -f apps/api/Dockerfile -t agentforge-api .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY apps/core /core
COPY apps/api/pyproject.toml apps/api/README.md ./
COPY apps/api/app ./app
RUN pip install /core ".[s3]" \
 && useradd --create-home --uid 10001 agentforge \
 && mkdir -p /app/storage && chown agentforge /app/storage

# never run as root: a bug in the app can't take over the container
USER agentforge
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"
# --proxy-headers: trust X-Forwarded-Proto from the load balancer so links use https.
# Scale with more containers (each one process) rather than workers inside one container.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
