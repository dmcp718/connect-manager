# terraform/modules/ecs-service-worker

Fargate worker service. Single-container ARQ worker, scale-to-zero capable, custom-metric autoscaling on ARQ queue depth (published by `module.queue-metric`).

## Why this exists separately from ecs-service-web

Workers and web have different shape:

| | Web | Worker |
|---|---|---|
| Inbound | ALB (TCP 8000) | None |
| Sidecars | lucidlink-api | None |
| Autoscaling metric | CPU | ARQ queue depth (CloudWatch custom) |
| Scale-to-zero | No (always-on for ALB targets) | Yes |
| Default capacity provider | `FARGATE` | `FARGATE_SPOT` |
| `stopTimeout` | 30s (fast drain) | 120s (longest-expected-job + buffer) |

Splitting the modules avoids forcing workers to inherit web's ALB plumbing and lets each one's defaults match its risk profile.

## Auto Scaling

Target-tracking policy on `var.metric_namespace`/`var.metric_name` with dimensions `{Env=<env>, Service=worker}`. Target value is **average queue depth per worker**:

- 0 workers + queue=10 → scale out toward `ceil(10 / target_queue_depth_per_worker)` (capped at `max_count`).
- N workers + queue=0 → scale in over `scale_in_cooldown`.
- `min_count=0` (default) is the scale-to-zero floor.

`scale_out_cooldown=30` (react fast to bursts), `scale_in_cooldown=300` (don't thrash on a momentary lull).

## FARGATE_SPOT default

Workers default to SPOT because:
- Mid-job interruption is safe (ARQ + the `processed_jobs` dedup table guarantee idempotency on retry).
- SPOT is ~70% cheaper.
- The CD deploy already drains tasks via SIGTERM → `app/services/shutdown.py`'s ARQ-finish hook → 120s `stopTimeout`.

Set `use_fargate_spot=false` for prod tiers where SPOT interruption is operationally inconvenient.

## Inputs / Outputs

See `variables.tf` and `outputs.tf` — same shape as `ecs-service-web` minus the ALB / sidecar fields plus the queue-metric ones.
