FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir \
    "fastapi>=0.109.0" \
    "uvicorn[standard]>=0.27.0" \
    "python-multipart>=0.0.6" \
    "jinja2>=3.1.3" \
    "httpx>=0.26.0" \
    "boto3>=1.34.0" \
    "arq>=0.26.0" \
    "python-jose[cryptography]>=3.3.0" \
    "passlib>=1.7.4" \
    "bcrypt>=4.0.0,<4.2.0"

# Copy application code
COPY app/ .

# Create data directory for SQLite
RUN mkdir -p /data

ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/data
ENV VALKEY_HOST=valkey
ENV VALKEY_PORT=6379

# Authentication settings
# JWT_SECRET_KEY should be overridden in production (generate with: openssl rand -hex 32)
ENV JWT_SECRET_KEY=change-me-in-production
ENV ACCESS_TOKEN_EXPIRE_MINUTES=480
ENV COOKIE_SECURE=true
# Set to restrict registration to a specific email domain (optional)
# ENV ALLOWED_EMAIL_DOMAIN=example.com
# Set to disable self-registration (use invite-only)
# ENV DISABLE_REGISTRATION=true

EXPOSE 8000
