FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY app/pyproject.toml .
COPY app/ .

# Install Python dependencies
RUN pip install --no-cache-dir -e .

# Create data directory for SQLite
RUN mkdir -p /data

ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/data
ENV VALKEY_HOST=valkey
ENV VALKEY_PORT=6379

EXPOSE 8000
