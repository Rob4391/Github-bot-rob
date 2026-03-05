import unittest

from app.ai import Finding, ReviewResult

try:
    from app.bot import GitBot
except ModuleNotFoundError:
    GitBot = None


@unittest.skipIf(GitBot is None, "PyGithub is not installed in this environment.")
class TestBotSuggestions(unittest.TestCase):
    def setUp(self) -> None:
        # Helper-method tests only; full bot dependencies are not needed.
        self.bot = GitBot.__new__(GitBot)

    def test_normalize_suggestions_filters_infra_and_duplicates(self) -> None:
        suggestions = self.bot._normalize_suggestions(
            [
                "Verify AI provider configuration and credentials (for Vertex AI: GOOGLE_CLOUD_PROJECT, VERTEX_LOCATION, VERTEX_MODEL, and service account access).",
                "Add targeted unit tests for edge cases in parser module.",
                "add targeted unit tests for edge cases in parser module.",
                " ",
            ]
        )
        self.assertEqual(suggestions, ["Add targeted unit tests for edge cases in parser module."])

    def test_suggestions_from_result_falls_back_to_findings(self) -> None:
        result = ReviewResult(
            summary="ok",
            findings=[
                Finding(
                    id="F1",
                    severity="medium",
                    category="quality",
                    file="app/parser.py",
                    message="Broad exception catch detected.",
                    suggestion="Catch specific exceptions and keep stack traces.",
                ),
                Finding(
                    id="F2",
                    severity="low",
                    category="tests",
                    file="*",
                    message="Source code changed without matching test changes.",
                    suggestion="",
                ),
            ],
            approve=False,
            confidence=0.5,
            source="vertex",
            risk_score=60,
            check_results={"quality": False, "tests": False},
            suggestions=[],
            profile="balanced",
        )

        suggestions = self.bot._suggestions_from_result(result)
        self.assertTrue(any("app/parser.py" in item for item in suggestions))
        self.assertTrue(any("Address tests issue" in item for item in suggestions))


if __name__ == "__main__":
    unittest.main()
