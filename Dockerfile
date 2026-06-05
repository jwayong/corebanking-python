# Build stage
FROM python:3.14-slim AS builder
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY . .

# Runtime stage
FROM python:3.14-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/ /usr/local/
COPY --from=builder /app /app
WORKDIR /app
EXPOSE 8080
CMD ["uvicorn", "cbs.main:app", "--host", "0.0.0.0", "--port", "8080"]
