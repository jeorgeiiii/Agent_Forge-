FROM python:3.12-slim

WORKDIR /app
COPY apps/core /core
COPY apps/worker/pyproject.toml ./
COPY apps/worker/*.py ./
RUN pip install --no-cache-dir /core .

CMD ["python", "main.py"]
