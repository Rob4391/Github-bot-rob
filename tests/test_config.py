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


if __name__ == "__main__":
    unittest.main()

