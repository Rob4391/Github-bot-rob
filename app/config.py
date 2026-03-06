import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _optional_env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name, "")
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _enum_env(name: str, default: str, allowed: set[str]) -> str:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in allowed:
        return raw
    return default


def _load_github_app_private_key() -> str | None:
    inline_key = _optional_env("GITHUB_APP_PRIVATE_KEY")
    if inline_key:
        # Support newline-escaped key values in .env files.
        return inline_key.replace("\\n", "\n")

    key_path = _optional_env("GITHUB_APP_PRIVATE_KEY_PATH")
    if key_path:
        try:
            with open(key_path, "r", encoding="utf-8") as handle:
                return handle.read()
        except OSError as exc:
            raise RuntimeError(
                f"Unable to read GITHUB_APP_PRIVATE_KEY_PATH file: {key_path}"
            ) from exc
    return None


@dataclass(frozen=True)
class Settings:
    auth_mode: str
    github_token: str | None
    github_app_id: int | None
    github_app_private_key: str | None
    github_webhook_secret: str
    ai_provider: str
    llm_required: bool
    openai_api_key: str | None
    openai_model: str
    vertex_project: str | None
    vertex_location: str
    vertex_model: str
    vertex_timeout_seconds: int
    vertex_max_output_tokens: int
    vertex_temperature: float
    bot_name: str
    bot_command_prefix: str
    auto_approve_confidence: float
    max_pr_files: int
    max_patch_chars: int
    require_green_checks_for_approve: bool
    high_risk_threshold: int
    auto_fullcheck_on_pr_events: bool
    ai_meme_mode: str
    state_file: str

    @classmethod
    def from_env(cls) -> "Settings":
        # Vertex-only runtime: enforce provider and disable non-LLM fallback behavior.
        ai_provider = "vertex"
        openai_api_key = _optional_env("OPENAI_API_KEY")
        vertex_project = _optional_env("GOOGLE_CLOUD_PROJECT") or _optional_env("GCP_PROJECT")
        vertex_location = os.getenv("VERTEX_LOCATION", "us-central1").strip() or "us-central1"
        github_token = _optional_env("GITHUB_TOKEN")
        app_id_raw = _optional_env("GITHUB_APP_ID")
        github_app_id = int(app_id_raw) if app_id_raw else None
        github_app_private_key = _load_github_app_private_key()

        if github_app_id and github_app_private_key:
            auth_mode = "app"
        elif github_token:
            auth_mode = "token"
        else:
            raise RuntimeError(
                "Missing GitHub auth config. "
                "Set GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY(_PATH) or GITHUB_TOKEN."
            )

        return cls(
            auth_mode=auth_mode,
            github_token=github_token,
            github_app_id=github_app_id,
            github_app_private_key=github_app_private_key,
            github_webhook_secret=_require_env("GITHUB_WEBHOOK_SECRET"),
            ai_provider=ai_provider,
            llm_required=_bool_env("LLM_REQUIRED", True),
            openai_api_key=openai_api_key,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            vertex_project=vertex_project,
            vertex_location=vertex_location,
            vertex_model=os.getenv("VERTEX_MODEL", "gemini-2.0-flash-001"),
            vertex_timeout_seconds=int(os.getenv("VERTEX_TIMEOUT_SECONDS", "300")),
            vertex_max_output_tokens=int(os.getenv("VERTEX_MAX_OUTPUT_TOKENS", "512")),
            vertex_temperature=float(os.getenv("VERTEX_TEMPERATURE", "0.0")),
            bot_name=os.getenv("BOT_NAME", "gitbot-rob"),
            bot_command_prefix=os.getenv("BOT_COMMAND_PREFIX", "/gitbot"),
            auto_approve_confidence=float(
                os.getenv("AUTO_APPROVE_CONFIDENCE", "0.85")
            ),
            max_pr_files=int(os.getenv("MAX_PR_FILES", "60")),
            max_patch_chars=int(os.getenv("MAX_PATCH_CHARS", "8000")),
            require_green_checks_for_approve=_bool_env(
                "REQUIRE_GREEN_CHECKS_FOR_APPROVE", True
            ),
            high_risk_threshold=int(os.getenv("HIGH_RISK_THRESHOLD", "70")),
            auto_fullcheck_on_pr_events=_bool_env("AUTO_FULLCHECK_ON_PR_EVENTS", False),
            ai_meme_mode=_enum_env("AI_MEME_MODE", "on-demand", {"off", "on-demand", "always"}),
            state_file=os.getenv("STATE_FILE", ".gitbot_state.json"),
        )
