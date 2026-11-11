FROM python:3.12-slim

WORKDIR /app
COPY apps/core /core
COPY apps/api/pyproject.toml apps/api/README.md ./
COPY apps/api/app ./app
RUN pip install --no-cache-dir /core .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
