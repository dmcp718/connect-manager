#!/usr/bin/env bash
# local/bootstrap.sh — idempotent local dev stack for connect-manager.
#
# Brings up the inner-loop environment described in SPEC §11.5:
#   1. Verify ministack container is running on ministack_default network
#      (managed out-of-band; we warn + bail if absent — never start it).
#   2. docker compose up the auxiliary postgres/valkey/registry containers
#      on the same ministack_default network.
#   3. Create a 3-node kind cluster wired to ministack_default if absent.
#   4. Install platform add-ons via helm: NGINX ingress, External Secrets
#      Operator, KEDA. Each in its own namespace, idempotent upgrade.
#   5. Helm-install the connect-manager chart skeleton with
#      values-local.yaml in the `connect` namespace, IF the chart exists
#      yet (it lands in awsk-rp6.3+ — we no-op gracefully until then).
#   6. Smoke-probe http://localhost:8080/health.
#
# Re-runs are safe and fast: every step short-circuits when its target
# already exists.
#
# Required tools: docker, kind, kubectl, helm, curl. Missing tools cause
# a hard fail with install instructions; we deliberately do NOT auto-
# install — that is an operator decision.
set -euo pipefail

# ----- config ---------------------------------------------------------------

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_DIR="${REPO_ROOT}/local"

MINISTACK_CONTAINER="${MINISTACK_CONTAINER:-ministack}"
MINISTACK_NETWORK="${MINISTACK_NETWORK:-ministack_default}"

KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-connect}"
KIND_CONFIG="${LOCAL_DIR}/kind-cluster.yaml"
COMPOSE_FILE="${LOCAL_DIR}/docker-compose.local.yml"
VALUES_LOCAL="${LOCAL_DIR}/values-local.yaml"
CHART_DIR="${REPO_ROOT}/helm/connect-manager"

CONNECT_NAMESPACE="connect"
INGRESS_NAMESPACE="ingress-nginx"
ESO_NAMESPACE="external-secrets"
KEDA_NAMESPACE="keda"

# Add-on chart versions pinned for reproducibility. Bump deliberately.
INGRESS_NGINX_VERSION="${INGRESS_NGINX_VERSION:-4.11.3}"
ESO_VERSION="${ESO_VERSION:-0.10.4}"
KEDA_VERSION="${KEDA_VERSION:-2.15.2}"

POSTGRES_LOCAL_PASSWORD="${POSTGRES_LOCAL_PASSWORD:-connect}"

HEALTH_URL="${HEALTH_URL:-http://localhost:8080/health}"
HEALTH_HOST_HEADER="${HEALTH_HOST_HEADER:-connect.local}"

# ----- helpers --------------------------------------------------------------

log()   { printf '\033[1;34m[bootstrap]\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[bootstrap:warn]\033[0m %s\n' "$*" >&2; }
err()   { printf '\033[1;31m[bootstrap:err]\033[0m %s\n' "$*" >&2; }
die()   { err "$*"; exit 1; }

require_cmd() {
  local cmd="$1" install_hint="$2"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    err "required command '${cmd}' not found on PATH"
    err "install hint: ${install_hint}"
    return 1
  fi
}

check_prereqs() {
  local missing=0
  require_cmd docker  "https://docs.docker.com/engine/install/" || missing=1
  require_cmd kind    "go install sigs.k8s.io/kind@latest  OR  https://kind.sigs.k8s.io/docs/user/quick-start/#installation" || missing=1
  require_cmd kubectl "https://kubernetes.io/docs/tasks/tools/" || missing=1
  require_cmd helm    "https://helm.sh/docs/intro/install/" || missing=1
  require_cmd curl    "apt-get install -y curl" || missing=1
  if [[ "${missing}" -ne 0 ]]; then
    die "missing prerequisites — install the tools above and re-run"
  fi

  if ! docker info >/dev/null 2>&1; then
    die "docker daemon is not reachable (try: sudo systemctl start docker)"
  fi
}

# ----- step 1: ministack ----------------------------------------------------

ensure_ministack() {
  log "checking ministack container on network '${MINISTACK_NETWORK}'"
  if ! docker network inspect "${MINISTACK_NETWORK}" >/dev/null 2>&1; then
    die "docker network '${MINISTACK_NETWORK}' not found — ministack must be running first (managed out-of-band; not started by this script)"
  fi
  local state
  state="$(docker inspect -f '{{.State.Status}}' "${MINISTACK_CONTAINER}" 2>/dev/null || true)"
  if [[ -z "${state}" ]]; then
    die "ministack container '${MINISTACK_CONTAINER}' not present — start it manually before running bootstrap (image: ministack-full:latest, port 4566)"
  fi
  if [[ "${state}" != "running" ]]; then
    die "ministack container exists but is in state '${state}' — start it manually (this script never restarts ministack)"
  fi
  log "ministack ok (container=${MINISTACK_CONTAINER}, network=${MINISTACK_NETWORK})"
}

# ----- step 2: aux compose --------------------------------------------------

ensure_compose() {
  log "bringing up aux services (postgres + valkey + registry) via docker compose"
  # Use modern `docker compose` plugin; fall back to legacy docker-compose.
  local compose_cmd
  if docker compose version >/dev/null 2>&1; then
    compose_cmd=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    compose_cmd=(docker-compose)
  else
    die "neither 'docker compose' plugin nor 'docker-compose' binary found"
  fi
  "${compose_cmd[@]}" -f "${COMPOSE_FILE}" up -d
  log "aux services up"
}

# ----- step 3: kind cluster -------------------------------------------------

ensure_kind_cluster() {
  log "checking for existing kind cluster '${KIND_CLUSTER_NAME}'"
  if kind get clusters 2>/dev/null | grep -qx "${KIND_CLUSTER_NAME}"; then
    log "kind cluster '${KIND_CLUSTER_NAME}' already exists — reusing"
  else
    log "creating kind cluster on docker network '${MINISTACK_NETWORK}'"
    KIND_EXPERIMENTAL_DOCKER_NETWORK="${MINISTACK_NETWORK}" \
      kind create cluster --config "${KIND_CONFIG}"
  fi
  # kubectl context is named `kind-<cluster>` per kind convention.
  kubectl config use-context "kind-${KIND_CLUSTER_NAME}" >/dev/null
  log "waiting for kind nodes to be Ready"
  kubectl wait --for=condition=Ready nodes --all --timeout=180s >/dev/null
  log "kind cluster ready"
}

# ----- step 4: platform add-ons --------------------------------------------

ensure_helm_repos() {
  log "configuring helm repositories"
  helm repo add ingress-nginx     https://kubernetes.github.io/ingress-nginx     >/dev/null 2>&1 || true
  helm repo add external-secrets  https://charts.external-secrets.io             >/dev/null 2>&1 || true
  helm repo add kedacore          https://kedacore.github.io/charts              >/dev/null 2>&1 || true
  helm repo update >/dev/null
}

helm_upgrade_install() {
  # Wraps `helm upgrade --install` so re-runs are no-ops when the release
  # is already at the requested version (helm itself handles diffing).
  local release="$1" chart="$2" namespace="$3" version="$4"
  shift 4
  helm upgrade --install "${release}" "${chart}" \
    --namespace "${namespace}" --create-namespace \
    --version "${version}" \
    --wait --timeout 5m \
    "$@"
}

ensure_ingress_nginx() {
  log "installing/upgrading NGINX ingress controller (v${INGRESS_NGINX_VERSION})"
  # The kind cluster maps containerPort 80→hostPort 8080 on the
  # control-plane node tagged ingress-ready=true. NGINX runs as a
  # DaemonSet on that node and binds hostPorts 80/443.
  helm_upgrade_install \
    ingress-nginx ingress-nginx/ingress-nginx \
    "${INGRESS_NAMESPACE}" "${INGRESS_NGINX_VERSION}" \
    --set controller.kind=DaemonSet \
    --set controller.hostPort.enabled=true \
    --set controller.service.type=NodePort \
    --set controller.nodeSelector."ingress-ready"=true \
    --set-string controller.tolerations[0].key=node-role.kubernetes.io/control-plane \
    --set-string controller.tolerations[0].operator=Exists \
    --set-string controller.tolerations[0].effect=NoSchedule \
    --set controller.publishService.enabled=false \
    --set controller.admissionWebhooks.enabled=false
}

ensure_external_secrets() {
  log "installing/upgrading External Secrets Operator (v${ESO_VERSION})"
  helm_upgrade_install \
    external-secrets external-secrets/external-secrets \
    "${ESO_NAMESPACE}" "${ESO_VERSION}" \
    --set installCRDs=true
}

ensure_keda() {
  log "installing/upgrading KEDA (v${KEDA_VERSION})"
  helm_upgrade_install \
    keda kedacore/keda \
    "${KEDA_NAMESPACE}" "${KEDA_VERSION}"
}

# ----- step 5: connect-manager chart ---------------------------------------

ensure_connect_namespace() {
  if ! kubectl get namespace "${CONNECT_NAMESPACE}" >/dev/null 2>&1; then
    log "creating namespace '${CONNECT_NAMESPACE}'"
    kubectl create namespace "${CONNECT_NAMESPACE}" >/dev/null
  fi
}

ensure_postgres_local_secret() {
  # values-local.yaml expects a Secret named `connect-postgres-local`
  # with key `password`. We create it idempotently so the data plane is
  # not coupled to ESO/Secrets Manager readiness in the local loop.
  log "ensuring local postgres password Secret"
  kubectl -n "${CONNECT_NAMESPACE}" create secret generic connect-postgres-local \
    --from-literal=password="${POSTGRES_LOCAL_PASSWORD}" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
}

install_connect_chart() {
  if [[ ! -d "${CHART_DIR}" ]]; then
    warn "chart directory '${CHART_DIR}' does not exist yet — skipping helm install"
    warn "this is expected until awsk-rp6.3 (chart skeleton) lands"
    return 0
  fi
  log "helm-installing connect-manager from ${CHART_DIR}"
  helm upgrade --install connect "${CHART_DIR}" \
    --namespace "${CONNECT_NAMESPACE}" --create-namespace \
    -f "${VALUES_LOCAL}" \
    --wait --timeout 5m || {
    warn "helm install reported failure — chart skeleton may not have all components ready yet"
    warn "see: kubectl -n ${CONNECT_NAMESPACE} get pods"
  }
}

# ----- step 6: smoke probe --------------------------------------------------

smoke_probe() {
  if [[ ! -d "${CHART_DIR}" ]]; then
    log "skipping smoke probe — connect chart not installed (chart dir absent)"
    return 0
  fi
  log "smoke-probing ${HEALTH_URL} (Host: ${HEALTH_HOST_HEADER})"
  local attempts=0 max_attempts=20 status
  while (( attempts < max_attempts )); do
    status="$(curl -sS -o /dev/null -w '%{http_code}' \
      -H "Host: ${HEALTH_HOST_HEADER}" "${HEALTH_URL}" || echo "000")"
    if [[ "${status}" == "200" ]]; then
      log "smoke probe OK (HTTP ${status})"
      return 0
    fi
    attempts=$((attempts + 1))
    sleep 3
  done
  warn "smoke probe did not return 200 after ${max_attempts} attempts (last: ${status})"
  warn "this is expected while app code is unimplemented; investigate via:"
  warn "  kubectl -n ${CONNECT_NAMESPACE} get pods"
  warn "  kubectl -n ${CONNECT_NAMESPACE} logs deploy/connect-web"
  return 0
}

# ----- main -----------------------------------------------------------------

main() {
  log "starting local/bootstrap.sh (repo=${REPO_ROOT})"
  check_prereqs
  ensure_ministack
  ensure_compose
  ensure_kind_cluster
  ensure_helm_repos
  ensure_ingress_nginx
  ensure_external_secrets
  ensure_keda
  ensure_connect_namespace
  ensure_postgres_local_secret
  install_connect_chart
  smoke_probe
  log "bootstrap complete"
  log "kubectl context: kind-${KIND_CLUSTER_NAME}"
  log "ingress at:      http://localhost:8080  (Host: ${HEALTH_HOST_HEADER})"
  log "ministack at:    http://localhost:4566  (untouched)"
}

main "$@"
