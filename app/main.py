import hashlib
import hmac
import json
import logging

from fastapi import FastAPI, HTTPException, Request

from .ai import BotIntelligence
from .auth import GitHubClientFactory
from .bot import GitBot
from .config import Settings
from .github_ops import GitHubOps


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("gitbot")


settings = Settings.from_env()
client_factory = GitHubClientFactory(settings=settings)
github_ops = GitHubOps(client_factory=client_factory)
brain = BotIntelligence(openai_api_key=settings.openai_api_key, model=settings.openai_model)
bot = GitBot(settings=settings, github_ops=github_ops, brain=brain)

app = FastAPI(title="GitBot Rob PoC", version="0.1.0")

logger.info("GitHub auth mode: %s", settings.auth_mode)


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> None:
    if not signature_header:
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256 header")

    expected_digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    expected_signature = f"sha256={expected_digest}"
    if not hmac.compare_digest(expected_signature, signature_header):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


@app.get("/healthz")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request) -> dict:
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    event_name = request.headers.get("X-GitHub-Event", "")

    verify_signature(settings.github_webhook_secret, body, signature)
    if not event_name:
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Event header")

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    logger.info("Received event=%s action=%s", event_name, payload.get("action"))
    actions = bot.handle_event(event_name=event_name, payload=payload)
    logger.info("Actions: %s", actions)
    return {"ok": True, "event": event_name, "actions": actions}
