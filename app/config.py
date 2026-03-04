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
    openai_api_key: str | None
    openai_model: str
    bot_name: str
    bot_command_prefix: str
    auto_approve_confidence: float
    max_pr_files: int
    max_patch_chars: int

    @classmethod
    def from_env(cls) -> "Settings":
        openai_api_key = _optional_env("OPENAI_API_KEY")
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
            openai_api_key=openai_api_key,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            bot_name=os.getenv("BOT_NAME", "gitbot-rob"),
            bot_command_prefix=os.getenv("BOT_COMMAND_PREFIX", "/gitbot"),
            auto_approve_confidence=float(
                os.getenv("AUTO_APPROVE_CONFIDENCE", "0.85")
            ),
            max_pr_files=int(os.getenv("MAX_PR_FILES", "60")),
            max_patch_chars=int(os.getenv("MAX_PATCH_CHARS", "8000")),
        )
