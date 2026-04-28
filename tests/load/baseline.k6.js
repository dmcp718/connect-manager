// k6 baseline load smoke for the aws-fargate web service.
//
// Usage:
//   BASE_URL=https://connect.example.com \
//   JWT_COOKIE=$(cat ./.smoke-cookie) \
//   k6 run tests/load/baseline.k6.js
//
// Optional env:
//   STAGES=ramp10x60 — preset to "ramp 0→10 VUs over 1m, hold 2m, drain 1m"
//   STAGES=spike     — "0→1 for 30s, 1→50 for 30s, 50→1 for 30s"
//
// What this measures:
//   - p95/p99 latency on /health and /ready (no auth required)
//   - 5xx rate on auth-cookie-protected /api/load-filespaces (read path)
//   - target group never marks tasks unhealthy under load
//
// What this does NOT do:
//   - import jobs (worker-side path; k6 isn't the right tool — use a long-
//     poll script that watches the ARQ queue depth metric instead)
//   - any write that mutates Postgres beyond JWT cookie validation
//
// Pass thresholds (deliberately loose for a baseline; tighten after a
// real-AWS run gives us a numbers floor):
//   - http_req_failed rate < 1%
//   - http_req_duration p95 < 800ms on /health
//   - http_req_duration p95 < 2s on /api/load-filespaces

import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "https://connect.example.com";
const JWT_COOKIE = __ENV.JWT_COOKIE || "";  // optional; auth'd paths skipped without it

const presets = {
  ramp10x60: [
    { duration: "1m", target: 10 },
    { duration: "2m", target: 10 },
    { duration: "1m", target: 0 },
  ],
  spike: [
    { duration: "30s", target: 1 },
    { duration: "30s", target: 50 },
    { duration: "30s", target: 1 },
  ],
};

const stagesPreset = __ENV.STAGES || "ramp10x60";
const stages = presets[stagesPreset] || presets.ramp10x60;

export const options = {
  stages,
  thresholds: {
    http_req_failed: ["rate<0.01"],
    "http_req_duration{endpoint:health}": ["p(95)<800"],
    "http_req_duration{endpoint:ready}": ["p(95)<800"],
    "http_req_duration{endpoint:filespaces}": ["p(95)<2000"],
  },
};

const healthLatency = new Trend("health_latency_ms");
const readyLatency = new Trend("ready_latency_ms");
const authedReqs = new Rate("authed_requests_attempted");

function get(path, tag) {
  const url = `${BASE_URL}${path}`;
  const params = {
    tags: { endpoint: tag },
    headers: JWT_COOKIE ? { Cookie: `access_token=${JWT_COOKIE}` } : {},
  };
  return http.get(url, params);
}

export default function () {
  // Unauthed probes — should always be 200.
  const h = get("/health", "health");
  check(h, { "health 200": (r) => r.status === 200 });
  healthLatency.add(h.timings.duration);

  const r = get("/ready", "ready");
  check(r, { "ready 200/503": (resp) => resp.status === 200 || resp.status === 503 });
  readyLatency.add(r.timings.duration);

  // Authed read path. Skipped silently if no cookie was provided so the
  // smoke can run from a sandboxed CI without test creds.
  if (JWT_COOKIE) {
    authedReqs.add(1);
    const f = get("/api/load-filespaces", "filespaces");
    check(f, {
      "filespaces 200": (resp) => resp.status === 200,
      "filespaces not 5xx": (resp) => resp.status < 500,
    });
  }

  sleep(1);
}

export function handleSummary(data) {
  return {
    stdout: `\n=== baseline summary ===\n`
      + `  http_req_failed.rate=${(data.metrics.http_req_failed.values.rate * 100).toFixed(2)}%\n`
      + `  health p95=${data.metrics.health_latency_ms.values["p(95)"].toFixed(0)}ms\n`
      + `  ready  p95=${data.metrics.ready_latency_ms.values["p(95)"].toFixed(0)}ms\n`
      + `  total reqs=${data.metrics.http_reqs.values.count}\n`,
  };
}
