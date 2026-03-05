import os
import tempfile
import unittest
from unittest.mock import patch

from app.config import Settings


class TestSettings(unittest.TestCase):
    def test_app_mode_from_inline_key(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GITHUB_WEBHOOK_SECRET": "secret",
                "GITHUB_APP_ID": "12345",
                "GITHUB_APP_PRIVATE_KEY": "line1\\nline2",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.auth_mode, "app")
        self.assertEqual(settings.github_app_id, 12345)
        self.assertEqual(settings.github_app_private_key, "line1\nline2")

    def test_app_mode_from_key_path(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", delete=True) as handle:
            handle.write("pem-content")
            handle.flush()
            with patch.dict(
                os.environ,
                {
                    "GITHUB_WEBHOOK_SECRET": "secret",
                    "GITHUB_APP_ID": "999",
                    "GITHUB_APP_PRIVATE_KEY_PATH": handle.name,
                },
                clear=True,
            ):
                settings = Settings.from_env()
        self.assertEqual(settings.auth_mode, "app")
        self.assertEqual(settings.github_app_private_key, "pem-content")

    def test_token_mode_fallback(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GITHUB_WEBHOOK_SECRET": "secret",
                "GITHUB_TOKEN": "ghp_xxx",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.auth_mode, "token")
        self.assertEqual(settings.github_token, "ghp_xxx")

    def test_missing_auth_raises(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GITHUB_WEBHOOK_SECRET": "secret",
            },
            clear=True,
        ):
            with self.assertRaises(RuntimeError):
                Settings.from_env()

    def test_ai_provider_fields(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GITHUB_WEBHOOK_SECRET": "secret",
                "GITHUB_TOKEN": "ghp_xxx",
                "AI_PROVIDER": "vertex",
                "LLM_REQUIRED": "true",
                "GOOGLE_CLOUD_PROJECT": "my-gcp-project",
                "VERTEX_LOCATION": "us-east4",
                "VERTEX_MODEL": "gemini-2.0-flash-001",
                "VERTEX_TIMEOUT_SECONDS": "420",
                "VERTEX_MAX_OUTPUT_TOKENS": "768",
                "VERTEX_TEMPERATURE": "0.2",
                "AUTO_FULLCHECK_ON_PR_EVENTS": "true",
                "AI_MEME_MODE": "always",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.ai_provider, "vertex")
        self.assertTrue(settings.llm_required)
        self.assertEqual(settings.vertex_project, "my-gcp-project")
        self.assertEqual(settings.vertex_location, "us-east4")
        self.assertEqual(settings.vertex_model, "gemini-2.0-flash-001")
        self.assertEqual(settings.vertex_timeout_seconds, 420)
        self.assertEqual(settings.vertex_max_output_tokens, 768)
        self.assertEqual(settings.vertex_temperature, 0.2)
        self.assertTrue(settings.auto_fullcheck_on_pr_events)
        self.assertEqual(settings.ai_meme_mode, "always")

    def test_cost_defaults(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GITHUB_WEBHOOK_SECRET": "secret",
                "GITHUB_TOKEN": "ghp_xxx",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertFalse(settings.auto_fullcheck_on_pr_events)
        self.assertEqual(settings.ai_meme_mode, "on-demand")


if __name__ == "__main__":
    unittest.main()
