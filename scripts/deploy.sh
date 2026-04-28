#!/usr/bin/env bash
# scripts/deploy.sh — Path B operator deploy.
#
# Replicates what .github/workflows/aws-fargate.yml does in CI, but locally
# with the operator's AWS credentials. Use this when you've set
# `github_repo = ""` in terraform.tfvars (no GitHub Actions / OIDC role)
# and want to ship a new image to ECS without wiring up a CI/CD pipeline.
#
# Pre-conditions:
#   - terraform/ is applied. ECR repos `connect-web` and `connect-worker`
#     exist.
#   - aws CLI configured with permissions to push to ECR + register ECS
#     task definitions + update ECS services + run-task on the migrate
#     family.
#   - docker + jq on $PATH.
#
# What it does:
#   1. Build linux/amd64 + linux/arm64 multi-arch images (--target web
#      and --target worker) and push to ECR with the supplied tag.
#   2. Register a new revision of the migrate task definition pointing at
#      the new web image, run-task it, wait for it to stop, fail loudly
#      if exit code is non-zero.
#   3. Register new revisions of the web + worker task definitions
#      pointing at their respective new images, update both services
#      with --force-new-deployment, and `aws ecs wait services-stable`.
#
# Usage:
#   ./scripts/deploy.sh [tag]
#
#   tag — image tag to push (default: short git SHA). If you're in a
#         dirty working tree, pass an explicit tag to make the deploy
#         traceable.
#
# Examples:
#   ./scripts/deploy.sh                      # uses HEAD's short SHA
#   ./scripts/deploy.sh v0.1.2               # explicit version tag
#   IMAGE_TAG=hotfix-2026-04-28 ./scripts/deploy.sh

set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
# Export so every aws CLI call below picks up the right region without
# needing --region on every invocation. ~/.aws/config might point at a
# different region than the operator's deployed stack.
export AWS_DEFAULT_REGION="$REGION"
export AWS_REGION="$REGION"
CLUSTER="${CLUSTER_NAME:-connect-prod}"
WEB_SERVICE="${WEB_SERVICE:-connect-prod-web}"
WORKER_SERVICE="${WORKER_SERVICE:-connect-prod-worker}"
WEB_TASK_DEF_FAMILY="${WEB_TASK_DEF_FAMILY:-connect-prod-web}"
WORKER_TASK_DEF_FAMILY="${WORKER_TASK_DEF_FAMILY:-connect-prod-worker}"
MIGRATE_TASK_DEF_FAMILY="${MIGRATE_TASK_DEF_FAMILY:-connect-prod-migrate}"
WEB_REPO="${WEB_REPO:-connect-web}"
WORKER_REPO="${WORKER_REPO:-connect-worker}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── Resolve tag ─────────────────────────────────────────────────────────────

TAG="${1:-${IMAGE_TAG:-}}"
if [[ -z "$TAG" ]]; then
    if git -C "$REPO_ROOT" rev-parse --short HEAD &>/dev/null; then
        TAG="$(git -C "$REPO_ROOT" rev-parse --short HEAD)"
    else
        echo "error: not a git repo and no tag provided" >&2
        exit 1
    fi
fi
echo "→ deploying tag: $TAG"

# ── Resolve ECR registry + image URIs ──────────────────────────────────────

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
WEB_IMAGE="${REGISTRY}/${WEB_REPO}:${TAG}"
WORKER_IMAGE="${REGISTRY}/${WORKER_REPO}:${TAG}"
echo "→ web image:    ${WEB_IMAGE}"
echo "→ worker image: ${WORKER_IMAGE}"

# ── ECR login ──────────────────────────────────────────────────────────────

echo "→ ECR login"
aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null

# ── Build + push (multi-arch) ──────────────────────────────────────────────
#
# Buildx with --platform produces a manifest list covering both
# architectures so Fargate (which runs on amd64 by default but supports
# arm64 via Graviton) can pull the right one. Single-arch builds work
# too if you'd rather skip the QEMU emulation cost.

if ! docker buildx inspect connect-builder &>/dev/null; then
    docker buildx create --name connect-builder --driver docker-container --bootstrap >/dev/null
fi
docker buildx use connect-builder

echo "→ building + pushing connect-web"
docker buildx build \
    --platform "${BUILD_PLATFORMS:-linux/amd64,linux/arm64}" \
    --target web \
    --tag "$WEB_IMAGE" \
    --push \
    "$REPO_ROOT"

echo "→ building + pushing connect-worker"
docker buildx build \
    --platform "${BUILD_PLATFORMS:-linux/amd64,linux/arm64}" \
    --target worker \
    --tag "$WORKER_IMAGE" \
    --push \
    "$REPO_ROOT"

# ── Migrate ────────────────────────────────────────────────────────────────

echo "→ registering migrate task definition with new web image"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

aws ecs describe-task-definition \
    --task-definition "$MIGRATE_TASK_DEF_FAMILY" \
    --query 'taskDefinition' \
    > "$TMPDIR/migrate.json"

jq --arg img "$WEB_IMAGE" \
    '.containerDefinitions[0].image = $img
     | del(.taskDefinitionArn, .revision, .status, .requiresAttributes,
           .compatibilities, .registeredAt, .registeredBy)' \
    "$TMPDIR/migrate.json" > "$TMPDIR/migrate.new.json"

MIGRATE_TASK_DEF_ARN="$(aws ecs register-task-definition \
    --cli-input-json "file://$TMPDIR/migrate.new.json" \
    --query 'taskDefinition.taskDefinitionArn' --output text)"

echo "→ resolving private subnets + web tasks security group"
SUBNETS="$(aws ec2 describe-subnets \
    --filters "Name=tag:Name,Values=${CLUSTER}-private-*" \
    --query 'Subnets[].SubnetId' --output text | tr '\t' ' ')"
SG="$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=${CLUSTER}-web-tasks" \
    --query 'SecurityGroups[0].GroupId' --output text)"

if [[ -z "$SUBNETS" || -z "$SG" ]]; then
    echo "error: could not resolve subnets ($SUBNETS) or web tasks SG ($SG)" >&2
    exit 1
fi

echo "→ running migrate task"
NETWORK_CONFIG="awsvpcConfiguration={subnets=[${SUBNETS// /,}],securityGroups=[${SG}],assignPublicIp=DISABLED}"
TASK_ARN="$(aws ecs run-task \
    --cluster "$CLUSTER" \
    --task-definition "$MIGRATE_TASK_DEF_ARN" \
    --launch-type FARGATE \
    --network-configuration "$NETWORK_CONFIG" \
    --query 'tasks[0].taskArn' --output text)"
echo "  task ARN: $TASK_ARN"

aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$TASK_ARN"

EXIT_CODE="$(aws ecs describe-tasks \
    --cluster "$CLUSTER" --tasks "$TASK_ARN" \
    --query 'tasks[0].containers[0].exitCode' --output text)"
if [[ "$EXIT_CODE" != "0" ]]; then
    echo "error: migrate task exited with code $EXIT_CODE" >&2
    aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" >&2
    exit 1
fi
echo "  migrate succeeded"

# ── Deploy web + worker ────────────────────────────────────────────────────

deploy_service() {
    local family="$1"
    local service="$2"
    local image="$3"
    local container_name="$4"   # 'web' or 'worker' — index into containerDefinitions

    echo "→ registering new $family revision"
    aws ecs describe-task-definition \
        --task-definition "$family" \
        --query 'taskDefinition' \
        > "$TMPDIR/$family.json"

    jq --arg img "$image" --arg name "$container_name" \
        '(.containerDefinitions[] | select(.name == $name)).image = $img
         | del(.taskDefinitionArn, .revision, .status, .requiresAttributes,
               .compatibilities, .registeredAt, .registeredBy)' \
        "$TMPDIR/$family.json" > "$TMPDIR/$family.new.json"

    local new_arn
    new_arn="$(aws ecs register-task-definition \
        --cli-input-json "file://$TMPDIR/$family.new.json" \
        --query 'taskDefinition.taskDefinitionArn' --output text)"

    echo "  new task def: $new_arn"
    echo "→ updating $service"
    aws ecs update-service \
        --cluster "$CLUSTER" \
        --service "$service" \
        --task-definition "$new_arn" \
        --force-new-deployment \
        > /dev/null
}

deploy_service "$WEB_TASK_DEF_FAMILY"    "$WEB_SERVICE"    "$WEB_IMAGE"    "web"
deploy_service "$WORKER_TASK_DEF_FAMILY" "$WORKER_SERVICE" "$WORKER_IMAGE" "worker"

echo "→ waiting for services to reach steady state (timeout 15 min)"
aws ecs wait services-stable \
    --cluster "$CLUSTER" \
    --services "$WEB_SERVICE" "$WORKER_SERVICE"

echo "✓ deploy complete: tag=$TAG"
