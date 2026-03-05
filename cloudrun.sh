#!/usr/bin/env bash
set -euo pipefail

required_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required environment variable: ${name}" >&2
    exit 1
  fi
}

to_bool() {
  local value="${1:-false}"
  case "${value,,}" in
    1|true|yes|on) echo "true" ;;
    *) echo "false" ;;
  esac
}

bump_semver() {
  local version="$1"
  local part="$2"
  if [[ ! "${version}" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
    echo "Invalid VERSION format: ${version}. Expected MAJOR.MINOR.PATCH" >&2
    exit 1
  fi
  local major="${BASH_REMATCH[1]}"
  local minor="${BASH_REMATCH[2]}"
  local patch="${BASH_REMATCH[3]}"
  case "${part}" in
    major)
      major=$((major + 1))
      minor=0
      patch=0
      ;;
    minor)
      minor=$((minor + 1))
      patch=0
      ;;
    patch)
      patch=$((patch + 1))
      ;;
    *)
      echo "Invalid BUMP_PART: ${part}. Use major, minor, or patch." >&2
      exit 1
      ;;
  esac
  echo "${major}.${minor}.${patch}"
}

ensure_secret() {
  local name="$1"
  if ! gcloud secrets describe "${name}" >/dev/null 2>&1; then
    if [[ "${SECRET_REPLICATION_POLICY}" == "user-managed" ]]; then
      gcloud secrets create "${name}" \
        --replication-policy="user-managed" \
        --locations="${SECRET_LOCATION}" >/dev/null
    else
      gcloud secrets create "${name}" --replication-policy="automatic" >/dev/null
    fi
  fi
}

decode_b64() {
  if base64 --help 2>&1 | grep -q -- "--decode"; then
    base64 --decode
  else
    base64 -D
  fi
}

encode_b64_file() {
  local path="$1"
  if base64 --help 2>&1 | grep -q -- "--wrap"; then
    base64 < "${path}" | tr -d "\n"
  else
    base64 -i "${path}" | tr -d "\n"
  fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_ENV_FILE="${SCRIPT_DIR}/.env"
ENV_FILE="${DEPLOY_ENV_FILE:-${1:-${DEFAULT_ENV_FILE}}}"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

if [[ -z "${GITHUB_APP_PRIVATE_KEY_B64:-}" ]] && [[ -n "${GITHUB_APP_PRIVATE_KEY_PATH:-}" ]]; then
  if [[ ! -f "${GITHUB_APP_PRIVATE_KEY_PATH}" ]]; then
    echo "GITHUB_APP_PRIVATE_KEY_PATH does not exist: ${GITHUB_APP_PRIVATE_KEY_PATH}" >&2
    exit 1
  fi
  GITHUB_APP_PRIVATE_KEY_B64="$(encode_b64_file "${GITHUB_APP_PRIVATE_KEY_PATH}")"
fi

required_env "PROJECT_ID"
required_env "REGION"
required_env "SERVICE_NAME"
required_env "REPO_NAME"
required_env "IMAGE_NAME"
required_env "GITHUB_APP_ID"
required_env "GITHUB_WEBHOOK_SECRET"
required_env "GITHUB_APP_PRIVATE_KEY_B64"

VERTEX_LOCATION="${VERTEX_LOCATION:-${REGION}}"
SECRET_REPLICATION_POLICY="${SECRET_REPLICATION_POLICY:-user-managed}"
SECRET_LOCATION="${SECRET_LOCATION:-${REGION}}"
VERTEX_MODEL="${VERTEX_MODEL:-gemini-2.0-flash-001}"
VERTEX_TIMEOUT_SECONDS="${VERTEX_TIMEOUT_SECONDS:-300}"
VERTEX_MAX_OUTPUT_TOKENS="${VERTEX_MAX_OUTPUT_TOKENS:-512}"
VERTEX_TEMPERATURE="${VERTEX_TEMPERATURE:-0.0}"
LLM_REQUIRED="${LLM_REQUIRED:-true}"
AUTO_FULLCHECK_ON_PR_EVENTS="${AUTO_FULLCHECK_ON_PR_EVENTS:-false}"
AI_MEME_MODE="${AI_MEME_MODE:-on-demand}"
BOT_COMMAND_PREFIX="${BOT_COMMAND_PREFIX:-/gitbot}"
VERSION_FILE="${VERSION_FILE:-${SCRIPT_DIR}/VERSION}"
if [[ ! -f "${VERSION_FILE}" ]]; then
  echo "Missing VERSION file: ${VERSION_FILE}" >&2
  exit 1
fi
BUMP_PART="${BUMP_PART:-patch}"
CURRENT_VERSION="$(tr -d '[:space:]' < "${VERSION_FILE}")"
if [[ -z "${CURRENT_VERSION}" ]]; then
  echo "VERSION file is empty: ${VERSION_FILE}" >&2
  exit 1
fi
IMAGE_TAG="$(bump_semver "${CURRENT_VERSION}" "${BUMP_PART}")"
printf "%s\n" "${IMAGE_TAG}" > "${VERSION_FILE}"
echo "Version bumped: ${CURRENT_VERSION} -> ${IMAGE_TAG}"
SERVICE_ACCOUNT_NAME="${SERVICE_ACCOUNT_NAME:-${SERVICE_NAME}-sa}"
SERVICE_ACCOUNT_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${IMAGE_NAME}:${IMAGE_TAG}"

gcloud config set project "${PROJECT_ID}" >/dev/null
gcloud services enable \
  aiplatform.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  secretmanager.googleapis.com \
  iam.googleapis.com >/dev/null

if ! gcloud artifacts repositories describe "${REPO_NAME}" --location="${REGION}" >/dev/null 2>&1; then
  gcloud artifacts repositories create "${REPO_NAME}" \
    --repository-format=docker \
    --location="${REGION}" >/dev/null
fi

if ! gcloud iam service-accounts describe "${SERVICE_ACCOUNT_EMAIL}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${SERVICE_ACCOUNT_NAME}" >/dev/null
fi

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/aiplatform.user" >/dev/null
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/secretmanager.secretAccessor" >/dev/null
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/logging.logWriter" >/dev/null

ensure_secret "github-webhook-secret"
ensure_secret "github-app-private-key"

printf "%s" "${GITHUB_WEBHOOK_SECRET}" | gcloud secrets versions add "github-webhook-secret" --data-file=- >/dev/null
printf "%s" "${GITHUB_APP_PRIVATE_KEY_B64}" | decode_b64 | gcloud secrets versions add "github-app-private-key" --data-file=- >/dev/null

gcloud builds submit --tag="${IMAGE_URI}" .

gcloud run deploy "${SERVICE_NAME}" \
  --image="${IMAGE_URI}" \
  --region="${REGION}" \
  --service-account="${SERVICE_ACCOUNT_EMAIL}" \
  --allow-unauthenticated \
  --set-env-vars="AI_PROVIDER=vertex,LLM_REQUIRED=${LLM_REQUIRED},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},VERTEX_LOCATION=${VERTEX_LOCATION},VERTEX_MODEL=${VERTEX_MODEL},VERTEX_TIMEOUT_SECONDS=${VERTEX_TIMEOUT_SECONDS},VERTEX_MAX_OUTPUT_TOKENS=${VERTEX_MAX_OUTPUT_TOKENS},VERTEX_TEMPERATURE=${VERTEX_TEMPERATURE},AUTO_FULLCHECK_ON_PR_EVENTS=${AUTO_FULLCHECK_ON_PR_EVENTS},AI_MEME_MODE=${AI_MEME_MODE},BOT_COMMAND_PREFIX=${BOT_COMMAND_PREFIX},STATE_FILE=/tmp/.gitbot_state.json,GITHUB_APP_ID=${GITHUB_APP_ID}" \
  --set-secrets="GITHUB_WEBHOOK_SECRET=github-webhook-secret:latest,GITHUB_APP_PRIVATE_KEY=github-app-private-key:latest" \
  >/dev/null

SERVICE_URL="$(gcloud run services describe "${SERVICE_NAME}" --region="${REGION}" --format='value(status.url)')"
echo "Deployed image: ${IMAGE_URI}"
echo "Cloud Run URL: ${SERVICE_URL}"
echo "GitHub webhook URL: ${SERVICE_URL}/webhook"
