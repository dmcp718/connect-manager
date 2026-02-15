#!/usr/bin/env bash
set -euo pipefail

# ── Config ──────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TF_DIR="$SCRIPT_DIR/terraform"
PROJECT_NAME="lucidlink-connect"

# ── Colors ──────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

info()    { echo -e "${BLUE}>>>${NC} $*"; }
success() { echo -e "${GREEN} ✓${NC} $*"; }
warn()    { echo -e "${YELLOW} !${NC} $*"; }
error()   { echo -e "${RED} ✗${NC} $*" >&2; }
die()     { error "$*"; exit 1; }

# ── Helpers ─────────────────────────────────────────────────────
check_prereqs() {
  command -v aws >/dev/null 2>&1 || die "AWS CLI not found. Install: https://aws.amazon.com/cli/"
  command -v terraform >/dev/null 2>&1 || die "Terraform not found. Install: https://developer.hashicorp.com/terraform/install"
  aws sts get-caller-identity >/dev/null 2>&1 || die "AWS credentials not configured. Run: aws configure"
}

get_region() {
  if [[ -f "$TF_DIR/terraform.tfvars" ]]; then
    grep -oE 'aws_region\s*=\s*"[^"]+"' "$TF_DIR/terraform.tfvars" | grep -oE '"[^"]+"' | tr -d '"' || echo "us-east-1"
  else
    echo "us-east-1"
  fi
}

get_instance_id() {
  local region="$1"
  aws ec2 describe-instances \
    --filters "Name=tag:Project,Values=$PROJECT_NAME" "Name=instance-state-name,Values=running" \
    --query "Reservations[0].Instances[0].InstanceId" \
    --output text --region "$region" 2>/dev/null
}

upload_app() {
  local bucket="$1" region="$2"

  info "Packaging application..."
  tar -czf /tmp/lucidlink-connect-app.tar.gz \
    --exclude='.git' \
    --exclude='terraform' \
    --exclude='.env' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.DS_Store' \
    --exclude='plans' \
    --exclude='node_modules' \
    --exclude='*.tfstate*' \
    -C "$SCRIPT_DIR" .

  info "Uploading to s3://$bucket/app/latest.tar.gz..."
  aws s3 cp /tmp/lucidlink-connect-app.tar.gz "s3://$bucket/app/latest.tar.gz" \
    --region "$region" --no-cli-pager >/dev/null
  rm -f /tmp/lucidlink-connect-app.tar.gz
  success "Application uploaded"
}

ssm_run() {
  local instance_id="$1" region="$2"
  shift 2
  local commands=("$@")

  # Build JSON commands array
  local json_cmds="["
  for i in "${!commands[@]}"; do
    [[ $i -gt 0 ]] && json_cmds+=","
    # Escape double quotes in command
    local escaped="${commands[$i]//\"/\\\"}"
    json_cmds+="\"$escaped\""
  done
  json_cmds+="]"

  local cmd_id
  cmd_id=$(aws ssm send-command \
    --instance-ids "$instance_id" \
    --document-name "AWS-RunShellScript" \
    --parameters "{\"commands\":$json_cmds}" \
    --timeout-seconds 300 \
    --region "$region" \
    --output text \
    --query "Command.CommandId" \
    --no-cli-pager)

  # Poll for completion
  info "Waiting for command to complete..."
  local status=""
  for i in {1..60}; do
    sleep 3
    local result
    result=$(aws ssm get-command-invocation \
      --command-id "$cmd_id" \
      --instance-id "$instance_id" \
      --region "$region" \
      --output json \
      --no-cli-pager 2>/dev/null) || continue

    status=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin)['Status'])" 2>/dev/null || echo "")

    if [[ "$status" == "Success" ]]; then
      echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('StandardOutputContent',''))"
      return 0
    elif [[ "$status" == "Failed" || "$status" == "TimedOut" || "$status" == "Cancelled" ]]; then
      echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('StandardOutputContent',''))"
      echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('StandardErrorContent',''))" >&2
      return 1
    fi
  done
  die "Command timed out after 3 minutes"
}

# ── Setup ───────────────────────────────────────────────────────
cmd_setup() {
  check_prereqs

  echo ""
  echo -e "${BOLD}LucidLink Connect Manager - AWS Setup${NC}"
  echo "======================================"
  echo ""

  read -rp "AWS Region [us-east-1]: " AWS_REGION
  AWS_REGION="${AWS_REGION:-us-east-1}"

  read -rp "Domain (e.g., connect.example.com): " DOMAIN
  [[ -z "$DOMAIN" ]] && die "Domain is required"

  read -rp "Route 53 Hosted Zone ID: " ZONE_ID
  [[ -z "$ZONE_ID" ]] && die "Zone ID is required"

  read -rp "Admin Email [admin@localhost]: " ADMIN_EMAIL
  ADMIN_EMAIL="${ADMIN_EMAIL:-admin@localhost}"

  read -rsp "Admin Password (min 8 chars, blank to generate): " ADMIN_PASSWORD
  echo ""
  if [[ -z "$ADMIN_PASSWORD" ]]; then
    ADMIN_PASSWORD=$(openssl rand -base64 16 | tr -d '=/+' | head -c 16)
    success "Generated password: $ADMIN_PASSWORD"
  fi

  read -rp "Instance Type [t3.small]: " INSTANCE_TYPE
  INSTANCE_TYPE="${INSTANCE_TYPE:-t3.small}"

  read -rp "Restrict access to CIDR (blank for public): " ALLOWED_CIDRS
  ALLOWED_CIDRS="${ALLOWED_CIDRS:-0.0.0.0/0}"

  # Generate JWT secret
  JWT_SECRET=$(openssl rand -hex 32)

  # Store secrets in SSM
  echo ""
  info "Storing secrets in SSM Parameter Store..."

  store_ssm() {
    local name="$1" value="$2" type="${3:-SecureString}"
    aws ssm put-parameter \
      --name "/${PROJECT_NAME}/$name" \
      --value "$value" \
      --type "$type" \
      --overwrite \
      --region "$AWS_REGION" \
      --no-cli-pager >/dev/null
    success "/${PROJECT_NAME}/$name"
  }

  store_ssm "jwt-secret-key" "$JWT_SECRET"
  store_ssm "admin-email" "$ADMIN_EMAIL" "String"
  store_ssm "admin-password" "$ADMIN_PASSWORD"
  store_ssm "domain" "$DOMAIN" "String"

  # Format allowed_cidrs as HCL
  local cidrs_hcl=""
  IFS=',' read -ra CIDR_ARRAY <<< "$ALLOWED_CIDRS"
  for cidr in "${CIDR_ARRAY[@]}"; do
    cidr=$(echo "$cidr" | xargs)
    cidrs_hcl+="\"$cidr\", "
  done
  cidrs_hcl="[${cidrs_hcl%, }]"

  # Create terraform.tfvars
  info "Creating terraform.tfvars..."
  cat > "$TF_DIR/terraform.tfvars" << EOF
aws_region      = "$AWS_REGION"
domain          = "$DOMAIN"
route53_zone_id = "$ZONE_ID"
instance_type   = "$INSTANCE_TYPE"
allowed_cidrs   = $cidrs_hcl
EOF
  success "terraform/terraform.tfvars"

  # Terraform init
  echo ""
  info "Initializing Terraform..."
  terraform -chdir="$TF_DIR" init -input=false

  echo ""
  success "Setup complete!"
  echo ""
  echo "  Next steps:"
  echo "    ./deploy.sh plan     # Preview infrastructure"
  echo "    ./deploy.sh deploy   # Deploy"
  echo ""
}

# ── Plan ────────────────────────────────────────────────────────
cmd_plan() {
  check_prereqs
  [[ -f "$TF_DIR/terraform.tfvars" ]] || die "Run './deploy.sh setup' first"
  terraform -chdir="$TF_DIR" plan
}

# ── Deploy ──────────────────────────────────────────────────────
cmd_deploy() {
  check_prereqs
  [[ -f "$TF_DIR/terraform.tfvars" ]] || die "Run './deploy.sh setup' first"

  local region
  region=$(get_region)

  info "Deploying infrastructure..."
  terraform -chdir="$TF_DIR" apply

  # Get outputs
  local bucket app_url
  bucket=$(terraform -chdir="$TF_DIR" output -raw deploy_bucket)
  app_url=$(terraform -chdir="$TF_DIR" output -raw app_url)

  # Upload application
  upload_app "$bucket" "$region"

  echo ""
  success "Deployment initiated!"
  echo ""
  echo "  URL:  $app_url"
  echo ""
  echo "  The instance is bootstrapping (~3-5 minutes)."
  echo "  Monitor progress:"
  echo "    ./deploy.sh logs boot    # Bootstrap logs"
  echo "    ./deploy.sh logs         # App logs"
  echo "    ./deploy.sh status       # Health check"
  echo ""
}

# ── Status ──────────────────────────────────────────────────────
cmd_status() {
  check_prereqs
  local region
  region=$(get_region)

  echo ""
  echo -e "${BOLD}LucidLink Connect Manager - Status${NC}"
  echo "===================================="

  local instance_id
  instance_id=$(get_instance_id "$region")

  if [[ "$instance_id" == "None" || -z "$instance_id" ]]; then
    warn "No running instances found"
    return
  fi

  echo ""
  info "Instance: $instance_id"
  aws ec2 describe-instances --instance-ids "$instance_id" \
    --query "Reservations[0].Instances[0].{State:State.Name,Type:InstanceType,AZ:Placement.AvailabilityZone,Launched:LaunchTime}" \
    --output table --region "$region" --no-cli-pager

  # ALB health
  echo ""
  local tg_arn
  tg_arn=$(aws elbv2 describe-target-groups \
    --names "${PROJECT_NAME}-tg" \
    --query "TargetGroups[0].TargetGroupArn" \
    --output text --region "$region" 2>/dev/null || echo "")

  if [[ -n "$tg_arn" && "$tg_arn" != "None" ]]; then
    info "Target health:"
    aws elbv2 describe-target-health \
      --target-group-arn "$tg_arn" \
      --output table --region "$region" --no-cli-pager
  fi

  # App URL
  local app_url
  app_url=$(terraform -chdir="$TF_DIR" output -raw app_url 2>/dev/null || echo "")
  [[ -n "$app_url" ]] && echo "" && info "URL: $app_url"
  echo ""
}

# ── SSH (SSM Session) ──────────────────────────────────────────
cmd_ssh() {
  check_prereqs
  local region
  region=$(get_region)

  local instance_id
  instance_id=$(get_instance_id "$region")
  [[ "$instance_id" == "None" || -z "$instance_id" ]] && die "No running instances found"

  info "Connecting to $instance_id via SSM..."
  aws ssm start-session --target "$instance_id" --region "$region"
}

# ── Logs ────────────────────────────────────────────────────────
cmd_logs() {
  check_prereqs
  local region
  region=$(get_region)
  local log_type="${1:-app}"

  local instance_id
  instance_id=$(get_instance_id "$region")
  [[ "$instance_id" == "None" || -z "$instance_id" ]] && die "No running instances found"

  case "$log_type" in
    app)
      info "Fetching app logs from $instance_id..."
      ssm_run "$instance_id" "$region" \
        "cd /opt/app && docker compose -f docker-compose.yml -f docker-compose.prod-shared.yml -f docker-compose.aws.yml logs --tail 200 --no-color"
      ;;
    boot)
      info "Fetching bootstrap logs from $instance_id..."
      ssm_run "$instance_id" "$region" \
        "cat /var/log/user-data.log"
      ;;
    *)
      echo "Usage: ./deploy.sh logs [app|boot]"
      return 1
      ;;
  esac
}

# ── Update ──────────────────────────────────────────────────────
cmd_update() {
  check_prereqs
  local region
  region=$(get_region)

  local bucket
  bucket=$(terraform -chdir="$TF_DIR" output -raw deploy_bucket 2>/dev/null || echo "")
  [[ -z "$bucket" ]] && die "Deploy bucket not found. Is infrastructure deployed?"

  local instance_id
  instance_id=$(get_instance_id "$region")
  [[ "$instance_id" == "None" || -z "$instance_id" ]] && die "No running instances found"

  # Upload new version
  upload_app "$bucket" "$region"

  # Update on instance
  info "Updating application on $instance_id..."
  ssm_run "$instance_id" "$region" \
    "set -e" \
    "cp /opt/app/.env /tmp/app-env-backup" \
    "rm -rf /opt/app.bak && mv /opt/app /opt/app.bak" \
    "mkdir -p /opt/app" \
    "aws s3 cp s3://${bucket}/app/latest.tar.gz /tmp/app.tar.gz --region ${region}" \
    "tar -xzf /tmp/app.tar.gz -C /opt/app && rm -f /tmp/app.tar.gz" \
    "cp /tmp/app-env-backup /opt/app/.env" \
    "cd /opt/app && docker compose -f docker-compose.yml -f docker-compose.prod-shared.yml -f docker-compose.aws.yml up -d --build" \
    "rm -rf /opt/app.bak" \
    "echo 'Update complete'"

  success "Application updated"
}

# ── Secrets ─────────────────────────────────────────────────────
cmd_secrets() {
  check_prereqs
  local region
  region=$(get_region)
  local action="${1:-list}"

  case "$action" in
    list)
      info "SSM parameters for /${PROJECT_NAME}/:"
      aws ssm get-parameters-by-path \
        --path "/${PROJECT_NAME}/" \
        --query "Parameters[].{Name:Name,Type:Type,Modified:LastModifiedDate}" \
        --output table --region "$region" --no-cli-pager
      ;;
    rotate-jwt)
      local new_secret
      new_secret=$(openssl rand -hex 32)
      aws ssm put-parameter \
        --name "/${PROJECT_NAME}/jwt-secret-key" \
        --value "$new_secret" \
        --type "SecureString" \
        --overwrite \
        --region "$region" --no-cli-pager >/dev/null
      success "JWT secret rotated"
      warn "Run './deploy.sh update' to apply. All sessions will be invalidated."
      ;;
    *)
      echo "Usage: ./deploy.sh secrets [list|rotate-jwt]"
      ;;
  esac
}

# ── Destroy ─────────────────────────────────────────────────────
cmd_destroy() {
  check_prereqs
  local region
  region=$(get_region)

  echo ""
  warn "This will destroy ALL infrastructure for ${PROJECT_NAME}"
  read -rp "Type 'destroy' to confirm: " confirm
  [[ "$confirm" != "destroy" ]] && die "Aborted"

  # Empty deploy bucket (required before deletion)
  local bucket
  bucket=$(terraform -chdir="$TF_DIR" output -raw deploy_bucket 2>/dev/null || echo "")
  if [[ -n "$bucket" ]]; then
    info "Emptying deploy bucket..."
    aws s3 rm "s3://$bucket" --recursive --region "$region" --no-cli-pager 2>/dev/null || true
  fi

  # Terraform destroy
  info "Destroying infrastructure..."
  terraform -chdir="$TF_DIR" destroy

  # Clean up SSM parameters
  info "Cleaning up SSM parameters..."
  for param in jwt-secret-key admin-email admin-password domain; do
    aws ssm delete-parameter \
      --name "/${PROJECT_NAME}/$param" \
      --region "$region" --no-cli-pager 2>/dev/null || true
  done

  success "All resources destroyed"
}

# ── Usage ───────────────────────────────────────────────────────
usage() {
  echo ""
  echo -e "${BOLD}LucidLink Connect Manager - AWS Deployment${NC}"
  echo "============================================"
  echo ""
  echo "Usage: ./deploy.sh <command>"
  echo ""
  echo "  setup          Configure secrets and initialize Terraform"
  echo "  plan           Preview infrastructure changes"
  echo "  deploy         Deploy infrastructure and application"
  echo "  destroy        Tear down all infrastructure"
  echo ""
  echo "  status         Show deployment status and health"
  echo "  ssh            Connect to instance via SSM"
  echo "  logs [type]    View logs (app|boot)"
  echo "  update         Push code changes to running instance"
  echo "  secrets [cmd]  Manage secrets (list|rotate-jwt)"
  echo ""
}

# ── Main ────────────────────────────────────────────────────────
case "${1:-}" in
  setup)   cmd_setup ;;
  plan)    cmd_plan ;;
  deploy)  cmd_deploy ;;
  destroy) cmd_destroy ;;
  status)  cmd_status ;;
  ssh)     cmd_ssh ;;
  logs)    cmd_logs "${2:-app}" ;;
  update)  cmd_update ;;
  secrets) cmd_secrets "${2:-list}" ;;
  *)       usage ;;
esac
