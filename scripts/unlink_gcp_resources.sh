#!/usr/bin/env bash
set -euo pipefail

# Unlink app-specific GCP resources created by cloudrun.sh.
# Usage:
#   ./scripts/unlink_gcp_resources.sh
#   ./scripts/unlink_gcp_resources.sh /path/to/.env

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
SECRETS=("github-webhook-secret" "github-app-private-key")
ROLES=("roles/aiplatform.user" "roles/secretmanager.secretAccessor" "roles/logging.logWriter")
APIS=(
  "aiplatform.googleapis.com"
  "artifactregistry.googleapis.com"
  "cloudbuild.googleapis.com"
  "run.googleapis.com"
  "secretmanager.googleapis.com"
  "iam.googleapis.com"
)

run_or_warn() {
  local msg="$1"
  shift
  echo "-> $msg"
  if "$@"; then
    echo "   ok"
  else
    echo "   skipped/failed (continuing)"
  fi
}

echo "This will delete/disable:"
echo "  PROJECT_ID=$PROJECT_ID"
echo "  Cloud Run service: $SERVICE_NAME (region: $REGION)"
echo "  Artifact Registry repo: $REPO_NAME (region: $REGION)"
echo "  Service account: $SERVICE_ACCOUNT_EMAIL"
echo "  Secrets: ${SECRETS[*]}"
echo "  APIs: ${APIS[*]}"
read -r -p "Type DELETE to continue: " CONFIRM
[[ "$CONFIRM" == "DELETE" ]] || { echo "Aborted."; exit 1; }

gcloud config set project "$PROJECT_ID" >/dev/null

run_or_warn "Delete Cloud Run service" \
  gcloud run services delete "$SERVICE_NAME" --region="$REGION" --project="$PROJECT_ID" --quiet

run_or_warn "Delete Artifact Registry repo (and images)" \
  gcloud artifacts repositories delete "$REPO_NAME" --location="$REGION" --project="$PROJECT_ID" --quiet

for secret_name in "${SECRETS[@]}"; do
  run_or_warn "Delete Secret Manager secret: $secret_name" \
    gcloud secrets delete "$secret_name" --project="$PROJECT_ID" --quiet
done

for role in "${ROLES[@]}"; do
  run_or_warn "Remove IAM binding: $role from $SERVICE_ACCOUNT_EMAIL" \
    gcloud projects remove-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:$SERVICE_ACCOUNT_EMAIL" \
      --role="$role" \
      --quiet
done

run_or_warn "Delete service account: $SERVICE_ACCOUNT_EMAIL" \
  gcloud iam service-accounts delete "$SERVICE_ACCOUNT_EMAIL" --project="$PROJECT_ID" --quiet

for api in "${APIS[@]}"; do
  run_or_warn "Disable API: $api" \
    gcloud services disable "$api" --project="$PROJECT_ID" --force --quiet
done

echo
echo "Cleanup complete for app-specific GCP resources in project: $PROJECT_ID"
echo "Optional local auth cleanup:"
echo "  gcloud auth application-default revoke -q || true"
