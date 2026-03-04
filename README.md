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
