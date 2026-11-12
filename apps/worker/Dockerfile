# AgentForge worker - runs workflow steps from Temporal. Build from the repo root.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY apps/core /core
COPY apps/worker/pyproject.toml ./
COPY apps/worker/*.py ./
RUN pip install /core . && useradd --create-home --uid 10001 agentforge

USER agentforge
CMD ["python", "main.py"]
