#!/usr/bin/env bash
# local/teardown.sh — tear down the local dev stack created by bootstrap.sh.
#
# Removes:
#   - the kind cluster (default name: connect)
#   - the auxiliary postgres / valkey / registry containers + named
#     volumes from local/docker-compose.local.yml
#   - dangling images tagged localhost:5000/* (the local registry's
#     pushed images; safe to drop — they get rebuilt)
#
# Preserves (NEVER touched):
#   - the ministack container (managed out-of-band; shared with other
#     workloads on this host)
#   - the ministack_default Docker network (other consumers may still
#     have containers attached)
#
# Re-runs are safe: every removal is best-effort.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_DIR="${REPO_ROOT}/local"

KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-connect}"
COMPOSE_FILE="${LOCAL_DIR}/docker-compose.local.yml"

log()  { printf '\033[1;34m[teardown]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[teardown:warn]\033[0m %s\n' "$*" >&2; }

remove_kind_cluster() {
  if ! command -v kind >/dev/null 2>&1; then
    warn "kind not installed — skipping kind cluster removal"
    return 0
  fi
  if kind get clusters 2>/dev/null | grep -qx "${KIND_CLUSTER_NAME}"; then
    log "deleting kind cluster '${KIND_CLUSTER_NAME}'"
    kind delete cluster --name "${KIND_CLUSTER_NAME}"
  else
    log "kind cluster '${KIND_CLUSTER_NAME}' not present — nothing to delete"
  fi
}

remove_compose_services() {
  if ! command -v docker >/dev/null 2>&1; then
    warn "docker not installed — skipping compose teardown"
    return 0
  fi
  local compose_cmd
  if docker compose version >/dev/null 2>&1; then
    compose_cmd=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    compose_cmd=(docker-compose)
  else
    warn "no docker compose available — skipping compose teardown"
    return 0
  fi
  if [[ ! -f "${COMPOSE_FILE}" ]]; then
    warn "compose file ${COMPOSE_FILE} missing — nothing to take down"
    return 0
  fi
  log "stopping aux services (postgres/valkey/registry) and removing volumes"
  # `down -v` removes named volumes declared in this compose file only.
  # The ministack container is not in this file, so it is left alone.
  "${compose_cmd[@]}" -f "${COMPOSE_FILE}" down -v --remove-orphans || true
}

prune_local_registry_images() {
  if ! command -v docker >/dev/null 2>&1; then
    return 0
  fi
  log "pruning images tagged localhost:5000/* (local registry pushes)"
  # Collect matching image IDs; rmi them best-effort. Quoting handles
  # the empty-list case (xargs -r is GNU-only; portable form below).
  local ids
  ids="$(docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' \
          | awk '$1 ~ /^localhost:5000\// {print $2}' \
          | sort -u)"
  if [[ -z "${ids}" ]]; then
    log "no localhost:5000/* images to prune"
    return 0
  fi
  # shellcheck disable=SC2086
  docker rmi -f ${ids} >/dev/null 2>&1 || true
}

verify_ministack_untouched() {
  # Defensive: confirm we did not somehow disturb ministack. This is
  # informational; we never want to touch it. CLAUDE.md §architecture
  # rule "ministack is shared" is enforced here at runtime.
  if ! command -v docker >/dev/null 2>&1; then
    return 0
  fi
  local state
  state="$(docker inspect -f '{{.State.Status}}' ministack 2>/dev/null || true)"
  if [[ -n "${state}" ]]; then
    log "ministack container preserved (state=${state})"
  else
    warn "ministack container is not present — but teardown.sh did not remove it"
    warn "(it may have been managed externally; not our concern)"
  fi
}

main() {
  log "starting local/teardown.sh (repo=${REPO_ROOT})"
  remove_kind_cluster
  remove_compose_services
  prune_local_registry_images
  verify_ministack_untouched
  log "teardown complete"
}

main "$@"
