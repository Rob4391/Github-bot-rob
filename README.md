# Github-bot-rob

Personal GitHub assistant bot PoC with GitHub App auth and interactive PR commands.

## Features

- Automatic PR review with risk scoring and severity-tagged findings
- Automatic FullCheck on PR open/update with posted summary
- Auto-approval / change-request decisions with optional CI gate
- Interactive commands from PR comments
- Semantic version bumping (`major` / `minor` / `patch`)
- Tag + release creation with changelog summary
- Smart merge release policy: auto-skip docs-only PRs, auto-pick bump type from PR labels/title/body/files, then create tag+release
- Auto labels (`high-risk`, `needs-tests`, `security`, `feature`, `bug`, `breaking-change`)
- Selective checks (`security`, `tests`, `docs`, `dependencies`, `size`, `quality`)
- Explain mode (`/gitbot explain F1`) and suggestions mode (`/gitbot suggest`)
- Conversation memory per PR (`/gitbot remember ...`, `/gitbot memory`)
- Policy profiles (`strict`, `balanced`, `fast`)
- Autofix in-place branch updates (`/gitbot autofix`)
- Vertex AI-generated meme response for command/operation outcomes (success/failure)

## Commands

- `/gitbot review`
- `/gitbot fullcheck`
- `/gitbot check security|tests|docs|dependencies|size|quality|all`
- `/gitbot explain <finding-id>`
- `/gitbot suggest` (fresh PR-aware suggestions)
- `/gitbot suggest cached` (reuse cached suggestions)
- `/gitbot remember <note>`
- `/gitbot memory`
- `/gitbot profile strict|balanced|fast`
- `/gitbot meme now`
- `/gitbot approve`
- `/gitbot merge [squash|merge|rebase]`
- `/gitbot close`
- `/gitbot reopen`
- `/gitbot bump patch|minor|major`
- `/gitbot release`
- `/gitbot autofix`

Autofix intentionally targets safe code rewrites (security/quality patterns). It does not auto-resolve
size/docs/tests/dependency-audit findings.

`/gitbot merge` is guarded: it requires recent review context, blocks when CI checks are not green
(if enabled), and blocks while high-severity findings are still open.

GitBot now posts a final meme response for command/operation outcomes (including failures) to keep
PR interactions more engaging.

## Project Structure

```text
app/
  main.py        # FastAPI webhook entrypoint
  bot.py         # Event routing + command orchestration
  ai.py          # Rule/LLM review engine, findings, risk score
  auth.py        # GitHub App / token auth client factory
  github_ops.py  # GitHub API operations
  state.py       # Persistent local bot state (.gitbot_state.json)
  semver.py      # Semantic version helper
  config.py      # Env settings
tests/
  test_semver.py
  test_config.py
  test_ai.py
  test_state.py
```

## Setup

Requires Python 3.10+.

### 1) Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Configure `.env`

Minimum required:

```bash
GITHUB_APP_ID=<your-app-id>
GITHUB_APP_PRIVATE_KEY_PATH=/absolute/path/to/app.private-key.pem
GITHUB_WEBHOOK_SECRET=<webhook-secret>
```

Optional:

```bash
GOOGLE_CLOUD_PROJECT=<your-gcp-project-id>
VERTEX_LOCATION=asia-south1
VERTEX_MODEL=gemini-2.0-flash-001
VERTEX_TIMEOUT_SECONDS=300
VERTEX_MAX_OUTPUT_TOKENS=512
VERTEX_TEMPERATURE=0.0
LLM_REQUIRED=true
BOT_COMMAND_PREFIX=/gitbot
AUTO_APPROVE_CONFIDENCE=0.85
REQUIRE_GREEN_CHECKS_FOR_APPROVE=true
HIGH_RISK_THRESHOLD=70
AUTO_FULLCHECK_ON_PR_EVENTS=false
AI_MEME_MODE=on-demand
MAX_PR_FILES=25
MAX_PATCH_CHARS=4000
MIN_INSTANCES=1
CPU=1
MEMORY=1Gi
CONCURRENCY=40
REQUEST_TIMEOUT=120
STATE_FILE=.gitbot_state.json
```

AI mode:

- Runtime is enforced to `Vertex AI only`.
- Fallback-to-rules is disabled in app runtime.
- PR review file ingestion is uncapped in bot runtime (all changed files/patches are sent to Vertex in batches).
- Cost controls:
  - `AUTO_FULLCHECK_ON_PR_EVENTS=false` skips automatic PR-open/sync reviews (run `/gitbot review` or `/gitbot fullcheck` manually).
  - `AI_MEME_MODE=off|on-demand|always` controls AI meme calls.
  - `MAX_PR_FILES` and `MAX_PATCH_CHARS` cap review payload sent to Vertex (lower = faster/cheaper).
  - `MIN_INSTANCES`, `CPU`, `MEMORY`, `CONCURRENCY`, `REQUEST_TIMEOUT` tune Cloud Run latency/throughput.

### Cost-Optimized Routing

Static/non-AI operations:

- `/gitbot approve`
- `/gitbot merge [squash|merge|rebase]`
- `/gitbot close`, `/gitbot reopen`
- `/gitbot bump patch|minor|major`
- `/gitbot release`
- `/gitbot remember`, `/gitbot memory`, `/gitbot profile`
- `/gitbot autofix` (safe deterministic rewrites from findings)

Vertex AI operations:

- `/gitbot review`
- `/gitbot fullcheck`
- `/gitbot check security|tests|docs|dependencies|size|quality|all`
- `/gitbot meme now` (and optional auto memes when `AI_MEME_MODE=always`)

Auth mode:

- `app` mode is used when `GITHUB_APP_ID` + app private key are present.
- `token` mode is fallback if only `GITHUB_TOKEN` is set.

### 3) Run locally

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8080
```

### 3.1) Vertex AI integration

Use Application Default Credentials locally:

```bash
gcloud auth application-default login
```

Set in `.env`:

```bash
GOOGLE_CLOUD_PROJECT=<your-gcp-project-id>
VERTEX_LOCATION=asia-south1
VERTEX_MODEL=gemini-2.0-flash-001
VERTEX_TIMEOUT_SECONDS=300
VERTEX_MAX_OUTPUT_TOKENS=512
VERTEX_TEMPERATURE=0.0
```

Required IAM role on the runtime identity:

- `roles/aiplatform.user`

### 3.2) Deploy to Cloud Run + Vertex AI

Fill required deploy values in root `.env` (`PROJECT_ID`, `REGION`, `SERVICE_NAME`,
`REPO_NAME`, `IMAGE_NAME`, `GITHUB_APP_ID`, `GITHUB_WEBHOOK_SECRET`,
`GITHUB_APP_PRIVATE_KEY_PATH`), then deploy:

```bash
./cloudrun.sh
```

Image tagging behavior:

- Every deploy automatically bumps `VERSION` and uses it as image tag.
- Default bump is `patch` (for example `0.1.2 -> 0.1.3`).
- Set `BUMP_PART=minor` or `BUMP_PART=major` when needed.

Optional: use a custom env file path:

```bash
./cloudrun.sh /absolute/path/to/my-cloudrun.env
```

### 4) Alternative: expose webhook with ngrok

```bash
ngrok http 8080
```

Webhook URL format:

`https://<ngrok-domain>/webhook`

## GitHub App Configuration

Repository permissions:

- `Pull requests`: Read and write
- `Contents`: Read and write
- `Issues`: Read and write
- `Checks`: Read and write
- `Commit statuses`: Read-only
- `Metadata`: Read-only

Events:

- `Pull request`
- `Issue comment`
- `Check run`

Install app on your target repository after saving permissions.

## Testing Flow

1. Open any PR in installed repo.
2. Add comment `/gitbot review`.
3. On PR open/sync, GitBot already runs FullCheck automatically and posts a summary.
4. You can also run `/gitbot fullcheck` manually and open the created Check Run.
5. In Check Run, click **Autofix** action button (or run `/gitbot autofix`).
This applies safe autofix edits directly on the same PR branch.
6. Re-run `/gitbot fullcheck`.
7. For human-controlled merge, click **Merge PR** button in `GitBot FullCheck` check run,
or use `/gitbot merge squash`.
8. Try `/gitbot check security`.
9. Try `/gitbot explain F1` and `/gitbot suggest`.
10. Meme is always on now. Try `/gitbot review` to see meme output.
11. Optional manual meme: `/gitbot meme now`.
12. Try `/gitbot bump patch` then `/gitbot release`.

## Troubleshooting

- `401 Unauthorized` on `/webhook`:
  - Webhook secret mismatch.
  - Update secret in GitHub App and `.env`, then restart server.

- `Resource not accessible by integration`:
  - Missing GitHub App write permissions (usually `Contents: Read and write`).
  - Reinstall/refresh app permissions on repo after changing permissions.
