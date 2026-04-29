#!/usr/bin/env bash
# scripts/resume.sh — bring the aws-fargate stack back from a pause.sh idle.
#
# Starts RDS first (waits for `available`, ~3-7 min on us-west-2 t4g.micro),
# then scales web back to 2 (matching var.web_desired_count default).
# Worker stays at 0 — Application Auto Scaling lifts it as the ARQ queue
# fills (target tracking on connect_arq_queue_depth).

set -euo pipefail

REGION="${AWS_REGION:-us-west-2}"
export AWS_DEFAULT_REGION="$REGION"
export AWS_REGION="$REGION"
CLUSTER="${CLUSTER_NAME:-connect-prod}"
WEB_SERVICE="${WEB_SERVICE:-connect-prod-web}"
WEB_DESIRED="${WEB_DESIRED_COUNT:-2}"
RDS_ID="${RDS_INSTANCE_ID:-connect-prod}"

echo "→ starting RDS $RDS_ID"
state="$(aws rds describe-db-instances --db-instance-identifier "$RDS_ID" \
    --query 'DBInstances[0].DBInstanceStatus' --output text)"
case "$state" in
    stopped)
        aws rds start-db-instance --db-instance-identifier "$RDS_ID" \
            --query 'DBInstance.[DBInstanceIdentifier,DBInstanceStatus]' --output text
        ;;
    available)
        echo "  RDS already available"
        ;;
    starting|backing-up|configuring-enhanced-monitoring|modifying)
        echo "  RDS in transition state=$state, will wait"
        ;;
    *)
        echo "  RDS status=$state, cannot resume" >&2
        exit 1
        ;;
esac

echo "→ waiting for RDS to become available (timeout 15 min)"
aws rds wait db-instance-available --db-instance-identifier "$RDS_ID"
echo "  RDS available"

echo "→ scaling $WEB_SERVICE back to $WEB_DESIRED"
aws ecs update-service --cluster "$CLUSTER" --service "$WEB_SERVICE" \
    --desired-count "$WEB_DESIRED" --query 'service.[serviceName,desiredCount]' --output text

echo
echo "→ waiting for services to stabilize (timeout 15 min)"
aws ecs wait services-stable --cluster "$CLUSTER" --services "$WEB_SERVICE"
echo
echo "✓ stack resumed. worker stays at 0 (Application Auto Scaling lifts it on demand)."
echo "  test: curl https://\$DOMAIN/health"
