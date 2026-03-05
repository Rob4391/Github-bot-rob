import hashlib
import hmac
import json
import logging
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from .ai import BotIntelligence
from .auth import GitHubClientFactory
from .bot import GitBot
from .config import Settings
from .github_ops import GitHubOps
from .state import BotState


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("gitbot")


settings = Settings.from_env()
client_factory = GitHubClientFactory(settings=settings)
github_ops = GitHubOps(client_factory=client_factory)
brain = BotIntelligence(
    openai_api_key=settings.openai_api_key,
    model=settings.openai_model,
    ai_provider=settings.ai_provider,
    vertex_project=settings.vertex_project,
    vertex_location=settings.vertex_location,
    vertex_model=settings.vertex_model,
    vertex_timeout_seconds=settings.vertex_timeout_seconds,
    vertex_max_output_tokens=settings.vertex_max_output_tokens,
    vertex_temperature=settings.vertex_temperature,
    llm_required=settings.llm_required,
)
state = BotState(path=settings.state_file)
bot = GitBot(settings=settings, github_ops=github_ops, brain=brain, state=state)

app = FastAPI(title="GitBot Rob PoC", version="0.1.0")

logger.info("GitHub auth mode: %s", settings.auth_mode)
logger.info("AI provider mode: %s (enforced)", settings.ai_provider)
logger.info("Vertex project: %s", settings.vertex_project or "(auto from ADC)")
logger.info("Vertex location: %s", settings.vertex_location)
logger.info(
    "Vertex tuning: model=%s max_output_tokens=%s temperature=%s timeout=%ss",
    settings.vertex_model,
    settings.vertex_max_output_tokens,
    settings.vertex_temperature,
    settings.vertex_timeout_seconds,
)
logger.info("Auto fullcheck on PR events: %s", settings.auto_fullcheck_on_pr_events)
logger.info("AI meme mode: %s", settings.ai_meme_mode)
logger.info("State file: %s", settings.state_file)


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


def _process_event(event_name: str, payload: dict[str, Any]) -> None:
    try:
        actions = bot.handle_event(event_name=event_name, payload=payload)
        logger.info("Actions: %s", actions)
    except Exception:
        logger.exception("Failed to process event=%s action=%s", event_name, payload.get("action"))


@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks) -> dict:
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
    background_tasks.add_task(_process_event, event_name, payload)
    return {"ok": True, "queued": True, "event": event_name}
