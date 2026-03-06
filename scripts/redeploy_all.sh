#!/usr/bin/env bash
set -euo pipefail

# One-shot full infra + app redeploy wrapper.
# Usage:
#   ./scripts/redeploy_all.sh
#   ./scripts/redeploy_all.sh /absolute/path/to/.env
#   ./scripts/redeploy_all.sh --yes
#   ./scripts/redeploy_all.sh /absolute/path/to/.env --yes

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_ENV_FILE="${ROOT_DIR}/.env"

ENV_FILE="${DEFAULT_ENV_FILE}"
ASSUME_YES="false"

for arg in "$@"; do
  case "$arg" in
    --yes|-y)
      ASSUME_YES="true"
      ;;
    *)
      ENV_FILE="$arg"
      ;;
  esac
done

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env file not found: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

required_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required environment variable: $name" >&2
    exit 1
  fi
}

required_env "PROJECT_ID"
required_env "REGION"
required_env "SERVICE_NAME"
required_env "REPO_NAME"
required_env "IMAGE_NAME"
required_env "GITHUB_APP_ID"
required_env "GITHUB_WEBHOOK_SECRET"

if [[ -z "${GITHUB_APP_PRIVATE_KEY_B64:-}" ]] && [[ -z "${GITHUB_APP_PRIVATE_KEY_PATH:-}" ]]; then
  echo "Set either GITHUB_APP_PRIVATE_KEY_B64 or GITHUB_APP_PRIVATE_KEY_PATH in $ENV_FILE" >&2
  exit 1
fi

command -v gcloud >/dev/null 2>&1 || { echo "gcloud not found in PATH"; exit 1; }
if ! gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q .; then
  echo "No active gcloud account. Run: gcloud auth login" >&2
  exit 1
fi

echo "Ready to recreate infra + deploy:"
echo "  PROJECT_ID=${PROJECT_ID}"
echo "  REGION=${REGION}"
echo "  SERVICE_NAME=${SERVICE_NAME}"
echo "  REPO_NAME=${REPO_NAME}"
echo "  IMAGE_NAME=${IMAGE_NAME}"
echo "  ENV_FILE=${ENV_FILE}"

if [[ "$ASSUME_YES" != "true" ]]; then
  read -r -p "Type DEPLOY to continue: " CONFIRM
  [[ "$CONFIRM" == "DEPLOY" ]] || { echo "Aborted."; exit 1; }
fi

"${ROOT_DIR}/cloudrun.sh" "$ENV_FILE"
