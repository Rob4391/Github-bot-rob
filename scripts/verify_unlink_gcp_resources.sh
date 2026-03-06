#!/usr/bin/env bash
set -euo pipefail

# Verify app-specific GCP unlink cleanup.
# Usage:
#   ./scripts/verify_unlink_gcp_resources.sh
#   ./scripts/verify_unlink_gcp_resources.sh /absolute/path/to/.env

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_ENV_FILE="${ROOT_DIR}/.env"

ENV_FILE="${1:-${DEFAULT_ENV_FILE}}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  echo "Env file not found: $ENV_FILE (continuing with defaults/env)"
fi

command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found in PATH"; exit 1; }

: "${PROJECT_ID:=github-project-489308}"
: "${REGION:=asia-south1}"
: "${SERVICE_NAME:=github-bot-rob}"
: "${REPO_NAME:=github-bot-rob}"
: "${SERVICE_ACCOUNT_NAME:=github-bot-rob-sa}"

SERVICE_ACCOUNT_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
APIS=(
  "aiplatform.googleapis.com"
  "artifactregistry.googleapis.com"
  "cloudbuild.googleapis.com"
  "run.googleapis.com"
  "secretmanager.googleapis.com"
  "iam.googleapis.com"
)

FAILURES=0

pass() {
  echo "OK:   $1"
}

fail() {
  echo "FAIL: $1"
  FAILURES=$((FAILURES + 1))
}

echo "== API status (expected: DISABLED) =="
for api in "${APIS[@]}"; do
  if CLOUDSDK_CORE_DISABLE_PROMPTS=1 gcloud services list --enabled \
    --project="$PROJECT_ID" \
    --filter="config.name:$api" \
    --format="value(config.name)" | grep -q .; then
    fail "$api is still ENABLED"
  else
    pass "$api is disabled"
  fi
done

check_missing() {
  local label="$1"
  shift
  if CLOUDSDK_CORE_DISABLE_PROMPTS=1 "$@" >/dev/null 2>&1; then
    fail "$label still exists"
  else
    pass "$label missing"
  fi
}

echo
echo "== Resource status (expected: MISSING) =="
check_missing "Cloud Run service $SERVICE_NAME" \
  gcloud run services describe "$SERVICE_NAME" --region="$REGION" --project="$PROJECT_ID" --quiet
check_missing "Artifact Registry repo $REPO_NAME" \
  gcloud artifacts repositories describe "$REPO_NAME" --location="$REGION" --project="$PROJECT_ID" --quiet
check_missing "Secret github-webhook-secret" \
  gcloud secrets describe "github-webhook-secret" --project="$PROJECT_ID" --quiet
check_missing "Secret github-app-private-key" \
  gcloud secrets describe "github-app-private-key" --project="$PROJECT_ID" --quiet
check_missing "Service account $SERVICE_ACCOUNT_EMAIL" \
  gcloud iam service-accounts describe "$SERVICE_ACCOUNT_EMAIL" --project="$PROJECT_ID" --quiet

echo
if [[ "$FAILURES" -eq 0 ]]; then
  echo "Verification passed: all targeted resources are unlinked."
  exit 0
fi

echo "Verification failed: $FAILURES check(s) failed."
exit 1
