# tests/load

k6 scripts for the `aws-fargate` web service.

## baseline.k6.js

Smoke-level load test. Probes `/health`, `/ready`, and (optionally) `/api/load-filespaces`. Default preset: ramp 0→10 VUs over 1 min, hold 2 min, drain 1 min.

```bash
BASE_URL=https://connect.example.com k6 run tests/load/baseline.k6.js
```

To exercise the auth'd read path, capture a JWT cookie from a manual login first:

```bash
curl -c ./.smoke-cookie -d 'email=…&password=…' \
  https://connect.example.com/api/auth/login
JWT_COOKIE=$(grep access_token ./.smoke-cookie | awk '{print $7}') \
  k6 run tests/load/baseline.k6.js
```

To run the spike preset (0→50 over 30s):

```bash
STAGES=spike k6 run tests/load/baseline.k6.js
```

## Pass thresholds

Deliberately loose for a baseline run — tighten after the first real-AWS smoke produces a numbers floor.

| Metric | Threshold |
|---|---|
| `http_req_failed.rate` | < 1% |
| `http_req_duration{endpoint:health}` p95 | < 800ms |
| `http_req_duration{endpoint:ready}` p95 | < 800ms |
| `http_req_duration{endpoint:filespaces}` p95 | < 2s |

## What's NOT tested here

Worker-side / job-import latency is **not** k6 territory. Use the queue-depth CloudWatch metric + a watcher script that enqueues N jobs and measures end-to-end time. Tracked as a future test (file a bead if it becomes a priority).
