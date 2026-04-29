#!/usr/bin/env bash
# scripts/pause.sh — minimum-cost idle for the aws-fargate stack.
#
# Scales ECS web + worker services to 0 and stops the RDS instance.
# Saves ~$45-55/mo (Fargate task-hours + RDS compute). NOT zero — the
# remaining ~$70/mo for NAT Gateway, ALB, and ElastiCache cannot be
# stopped (only destroyed). For a true $0 idle, run `terraform destroy`
# (~25 min to re-apply on resume).
#
# RDS auto-resumes after 7 days regardless. Resume sooner with
# scripts/resume.sh.

set -euo pipefail

REGION="${AWS_REGION:-us-west-2}"
export AWS_DEFAULT_REGION="$REGION"
export AWS_REGION="$REGION"
CLUSTER="${CLUSTER_NAME:-connect-prod}"
WEB_SERVICE="${WEB_SERVICE:-connect-prod-web}"
WORKER_SERVICE="${WORKER_SERVICE:-connect-prod-worker}"
RDS_ID="${RDS_INSTANCE_ID:-connect-prod}"

echo "→ scaling $WEB_SERVICE to 0"
aws ecs update-service --cluster "$CLUSTER" --service "$WEB_SERVICE" \
    --desired-count 0 --query 'service.[serviceName,desiredCount]' --output text

echo "→ scaling $WORKER_SERVICE to 0"
aws ecs update-service --cluster "$CLUSTER" --service "$WORKER_SERVICE" \
    --desired-count 0 --query 'service.[serviceName,desiredCount]' --output text

echo "→ stopping RDS $RDS_ID"
state="$(aws rds describe-db-instances --db-instance-identifier "$RDS_ID" \
    --query 'DBInstances[0].DBInstanceStatus' --output text 2>/dev/null || echo missing)"
case "$state" in
    available)
        aws rds stop-db-instance --db-instance-identifier "$RDS_ID" \
            --query 'DBInstance.[DBInstanceIdentifier,DBInstanceStatus]' --output text
        ;;
    stopped|stopping)
        echo "  RDS already $state"
        ;;
    *)
        echo "  RDS status=$state, skipping stop" >&2
        ;;
esac

echo
echo "✓ paused. resume with scripts/resume.sh"
echo "  reminder: AWS auto-resumes RDS after 7 days; ALB+NAT+ElastiCache continue accruing"
