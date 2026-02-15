#!/bin/bash
set -euo pipefail
exec > >(tee /var/log/user-data.log) 2>&1
echo "=== LucidLink Connect Manager - Bootstrap ==="
echo "Started: $(date)"

REGION="${aws_region}"
PROJECT="${project_name}"

# ── Install Docker ──────────────────────────────────────────────
echo "Installing Docker..."
dnf install -y docker amazon-efs-utils
systemctl enable --now docker
usermod -aG docker ec2-user

# Install Docker Compose plugin
ARCH=$(uname -m)
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$${ARCH}" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
ln -sf /usr/local/lib/docker/cli-plugins/docker-compose /usr/local/bin/docker-compose

echo "Docker $(docker --version)"
echo "Compose $(docker compose version)"

# ── Mount EFS ───────────────────────────────────────────────────
echo "Mounting EFS ${efs_id}..."
mkdir -p /data
for i in {1..12}; do
  mount -t efs -o tls "${efs_id}":/ /data && break
  echo "  Mount attempt $i failed, retrying in 10s..."
  sleep 10
done
mount | grep -q "/data" || { echo "ERROR: Failed to mount EFS after 2 minutes"; exit 1; }
echo "${efs_id}:/ /data efs _netdev,tls 0 0" >> /etc/fstab

# Create persistent data directories
mkdir -p /data/app /data/valkey /data/lucidlink

# ── Download Application ────────────────────────────────────────
echo "Downloading application from S3..."
mkdir -p /opt/app
aws s3 cp "s3://${s3_bucket}/app/latest.tar.gz" /tmp/app.tar.gz --region "$REGION"
tar -xzf /tmp/app.tar.gz -C /opt/app
rm -f /tmp/app.tar.gz

# ── Create .env from SSM Parameters ────────────────────────────
echo "Loading configuration from SSM..."
get_param() {
  aws ssm get-parameter \
    --name "/$PROJECT/$1" \
    --with-decryption \
    --query "Parameter.Value" \
    --output text \
    --region "$REGION" 2>/dev/null || echo ""
}

cat > /opt/app/.env << ENVEOF
DOMAIN=$(get_param domain)
JWT_SECRET_KEY=$(get_param jwt-secret-key)
ADMIN_EMAIL=$(get_param admin-email)
ADMIN_PASSWORD=$(get_param admin-password)
COOKIE_SECURE=true
ENVIRONMENT=production
ENVEOF
chmod 600 /opt/app/.env

# ── Start Application ──────────────────────────────────────────
echo "Starting application..."
cd /opt/app
docker compose \
  -f docker-compose.yml \
  -f docker-compose.prod-shared.yml \
  -f docker-compose.aws.yml \
  up -d --build

# ── Create systemd service for auto-start on reboot ────────────
cat > /etc/systemd/system/lucidlink-connect.service << 'SVCEOF'
[Unit]
Description=LucidLink Connect Manager
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/app
ExecStart=/usr/local/bin/docker-compose -f docker-compose.yml -f docker-compose.prod-shared.yml -f docker-compose.aws.yml up -d
ExecStop=/usr/local/bin/docker-compose -f docker-compose.yml -f docker-compose.prod-shared.yml -f docker-compose.aws.yml down
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
SVCEOF
systemctl daemon-reload
systemctl enable lucidlink-connect.service

echo "=== Bootstrap complete: $(date) ==="
