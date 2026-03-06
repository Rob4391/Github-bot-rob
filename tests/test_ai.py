import unittest

from app.ai import BotIntelligence, ChangedFile


class TestBotIntelligence(unittest.TestCase):
    def setUp(self) -> None:
        self.brain = BotIntelligence(openai_api_key=None, model="gpt-4.1-mini")

    def test_review_returns_structured_findings(self) -> None:
        files = [
            ChangedFile(
                filename="app/x.py",
                status="modified",
                additions=10,
                deletions=2,
                patch="+print('debug')\n+except Exception:\n+pass\n",
            )
        ]
        result = self.brain.review_pull_request(
            title="test",
            description="",
            changed_files=files,
            profile="balanced",
        )
        self.assertGreaterEqual(result.risk_score, 1)
        self.assertGreaterEqual(len(result.findings), 1)
        self.assertTrue(result.findings[0].id.startswith("F"))

    def test_selective_check_runs_only_target_category(self) -> None:
        files = [
            ChangedFile(
                filename="app/x.py",
                status="modified",
                additions=10,
                deletions=2,
                patch="+print('debug')\n+except Exception:\n+pass\n",
            )
        ]
        result = self.brain.review_pull_request(
            title="test",
            description="",
            changed_files=files,
            profile="balanced",
            enabled_checks={"size"},
        )
        self.assertTrue(all(item.category == "size" for item in result.findings))

    def test_security_scan_ignores_markdown_and_gitbot_files(self) -> None:
        files = [
            ChangedFile(
                filename="README.md",
                status="modified",
                additions=5,
                deletions=0,
                patch="+private_key=abc\n+api_key=def\n",
            ),
            ChangedFile(
                filename=".gitbot/autofix-pr-1.md",
                status="added",
                additions=5,
                deletions=0,
                patch="+TODO item\n+FIXME item\n+private_key\n",
            ),
        ]
        result = self.brain.review_pull_request(
            title="docs update",
            description="",
            changed_files=files,
            profile="balanced",
        )
        self.assertTrue(all(item.category != "security" for item in result.findings))

    def test_security_scan_ignores_literal_eval_and_config_key_usage(self) -> None:
        files = [
            ChangedFile(
                filename="app/ai.py",
                status="modified",
                additions=6,
                deletions=0,
                patch='+if "eval(" in normalized_line:\n'
                '+if "exec(" in normalized_line:\n'
                '+pattern = r"token\\s*=\\s*[^&\\s]+"\n',
            ),
            ChangedFile(
                filename="app/auth.py",
                status="modified",
                additions=2,
                deletions=0,
                patch="+private_key=settings.github_app_private_key,\n",
            ),
        ]
        result = self.brain.review_pull_request(
            title="detector update",
            description="",
            changed_files=files,
            profile="balanced",
        )
        self.assertTrue(all(item.category != "security" for item in result.findings))

    def test_security_scan_detects_real_eval_and_secret_literal(self) -> None:
        files = [
            ChangedFile(
                filename="app/runtime.py",
                status="modified",
                additions=4,
                deletions=0,
                patch='+api_key = "abc1234567"\n+value = eval(user_input)\n',
            )
        ]
        result = self.brain.review_pull_request(
            title="runtime update",
            description="",
            changed_files=files,
            profile="balanced",
            enabled_checks={"security"},
        )
        security_messages = [item.message for item in result.findings if item.category == "security"]
        self.assertTrue(any("hardcoded secret" in msg.lower() for msg in security_messages))
        self.assertTrue(any("eval" in msg.lower() for msg in security_messages))

    def test_llm_required_returns_unavailable_when_provider_fails(self) -> None:
        brain = BotIntelligence(
            openai_api_key=None,
            model="gemini-2.0-flash-001",
            ai_provider="vertex",
            vertex_project=None,
            vertex_model="gemini-2.0-flash-001",
            llm_required=True,
        )
        files = [
            ChangedFile(
                filename="app/x.py",
                status="modified",
                additions=2,
                deletions=0,
                patch="+x = 1\n",
            )
        ]
        result = brain.review_pull_request(
            title="llm strict",
            description="",
            changed_files=files,
            profile="balanced",
        )
        self.assertEqual(result.source, "llm-unavailable")
        self.assertFalse(result.approve)
        self.assertEqual(result.risk_score, 100)


if __name__ == "__main__":
    unittest.main()
