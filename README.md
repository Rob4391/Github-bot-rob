# Github-bot-rob

PoC for a personal GitHub assistant bot that can:

- Review pull requests automatically
- Post review comments
- Auto-approve low-risk PRs
- Bump semantic versions
- Create version tags
- Create GitHub releases
- Accept chat-like commands via PR comments

## What This PoC Includes

- `FastAPI` webhook service (`/webhook`)
- GitHub API integration through `PyGithub`
- Production auth path: GitHub App JWT + installation access tokens
- Local fallback auth path: personal access token
- "Intelligence" layer:
  - Heuristic review engine (always available)
  - Optional LLM-powered review via OpenAI API
- Command interface from PR comments:
  - `/gitbot review`
  - `/gitbot approve`
  - `/gitbot bump patch|minor|major`
  - `/gitbot release`

## Project Structure

```text
app/
  main.py        # FastAPI entrypoint + signature verification
  bot.py         # Event routing + orchestration
  ai.py          # Intelligence layer (heuristics + optional LLM)
  auth.py        # GitHub App / token auth client factory
  github_ops.py  # GitHub operations (reviews, comments, tags, releases)
  semver.py      # Semantic version parsing and bump logic
  config.py      # Env-based settings
tests/
  test_semver.py
  test_config.py
```

## Quick Start

Requires Python 3.10+.

### 1) Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Configure environment

```bash
cp .env.example .env
```

Set values in `.env`:

- `GITHUB_WEBHOOK_SECRET`: shared secret for webhook signature verification
- `OPENAI_API_KEY` (optional): enables LLM review mode
- For production mode (recommended):
  - `GITHUB_APP_ID`
  - `GITHUB_APP_PRIVATE_KEY_PATH` (or `GITHUB_APP_PRIVATE_KEY` inline)
- For local fallback mode:
  - `GITHUB_TOKEN`

Auth mode selection:

- Uses `app` mode if `GITHUB_APP_ID` and app private key are present.
- Otherwise uses `token` mode if `GITHUB_TOKEN` is present.
- Startup fails if neither is configured.

### 3) Run service

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

### 4) Configure GitHub webhook

In your GitHub App settings:

- Payload URL: `https://<your-public-url>/webhook`
- Content type: `application/json`
- Secret: same value as `GITHUB_WEBHOOK_SECRET`
- Events:
  - `Pull requests`
  - `Issue comments`
- Install the app on your target repositories.

Recommended GitHub App permissions:

- Repository permissions:
  - `Pull requests`: Read and write
  - `Contents`: Read and write
  - `Issues`: Read and write
  - `Metadata`: Read-only

For local testing, expose your local port via ngrok or similar.

## Docker Hub + Global Hosting

Docker Hub stores your image only. To run globally, deploy that image on an always-on server.

### 1) Build and push image to Docker Hub

```bash
docker build -t <dockerhub-user>/github-bot-rob:latest .
docker push <dockerhub-user>/github-bot-rob:latest
```

### 2) Prepare server files

On your server, clone this repo and create runtime files:

```bash
cp .env.example .env
cp deploy/.env.docker.example deploy/.env.docker
mkdir -p secrets
```

Set these values:

- In `.env`:
  - `GITHUB_APP_ID`
  - `GITHUB_WEBHOOK_SECRET`
  - `GITHUB_APP_PRIVATE_KEY_PATH=/run/secrets/github-app.pem`
  - Optional `OPENAI_API_KEY`
- In `deploy/.env.docker`:
  - `DOMAIN` (for example: `bot.yourdomain.com`)
  - `EMAIL` (for TLS cert notices)
  - `GITBOT_IMAGE` (for example: `youruser/github-bot-rob:latest`)

Copy your GitHub App private key file to:

```bash
secrets/github-app.pem
```

### 3) Start service with HTTPS

```bash
set -a
source deploy/.env.docker
set +a
docker compose up -d
```

This starts:

- `gitbot` app container on internal port `8080`
- `caddy` reverse proxy on `80/443` with automatic HTTPS

### 4) Point GitHub webhook

In GitHub App settings:

- Webhook URL: `https://<DOMAIN>/webhook`
- Secret: exactly your `GITHUB_WEBHOOK_SECRET`

### 5) Update deployment after new image push

```bash
set -a
source deploy/.env.docker
set +a
docker compose pull
docker compose up -d
```

### Notes

- Persisted bot state is stored in docker volume `gitbot_data` (`STATE_FILE=/data/.gitbot_state.json`).
- Keep `secrets/github-app.pem` and `.env` out of git.
- Rotate webhook secret if it was ever exposed.

## Event Behavior

- PR opened/synchronized/reopened:
  - Bot reviews changed files
  - Posts a review (`APPROVE` or `REQUEST_CHANGES`)
- PR merged:
  - Bot bumps `VERSION` using labels:
    - `major` -> major bump
    - `minor` -> minor bump
    - default -> patch bump
  - Bot creates tag `v<version>`
  - Bot creates GitHub release
- PR comment commands:
  - `/gitbot review` -> rerun review
  - `/gitbot approve` -> force approval
  - `/gitbot bump patch|minor|major` -> bump `VERSION` on default branch
  - `/gitbot release` -> release current `VERSION`

## Notes

- This PoC now supports production-grade GitHub App auth flow.
- PAT mode remains for quick local experiments only.
- The bot only accepts commands from `OWNER`, `MEMBER`, or `COLLABORATOR`.
