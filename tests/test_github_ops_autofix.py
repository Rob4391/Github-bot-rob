import unittest

try:
    from app.github_ops import GitHubOps
except ModuleNotFoundError:
    GitHubOps = None


@unittest.skipIf(GitHubOps is None, "PyGithub is not installed in this environment.")
class TestGitHubOpsAutofix(unittest.TestCase):
    def test_python_autofix_applies_supported_changes(self) -> None:
        original = (
            "def run():\n"
            "    API_TOKEN = \"abcd1234\"\n"
            "    try:\n"
            "        print(\"debug\")\n"
            "    except Exception:\n"
            "        pass\n"
            "    value = \"TODO replace\"\n"
            "    note = \"FIXME soon\"\n"
        )
        findings = [
            {"message": "Debug logging found (print)."},
            {"message": "Broad exception catch detected."},
            {"message": "TODO marker found in changed code."},
            {"message": "FIXME marker found in changed code."},
            {"message": "Potential hardcoded secret detected."},
        ]

        updated, applied, unresolved = GitHubOps._apply_safe_autofixes(
            path="app/sample.py",
            content=original,
            findings=findings,
        )

        self.assertIn("import os\n", updated)
        self.assertIn('API_TOKEN = os.getenv("API_TOKEN", "")', updated)
        self.assertIn("except Exception as exc:", updated)
        self.assertIn("raise", updated)
        self.assertIn("pass  # gitbot: removed debug print", updated)
        self.assertNotIn("TODO", updated)
        self.assertNotIn("FIXME", updated)
        self.assertIn("TRACKED_TASK", updated)

        self.assertIn("remove_print", applied)
        self.assertIn("narrow_exception", applied)
        self.assertIn("secret_to_env", applied)
        self.assertEqual(unresolved, [])

    def test_js_autofix_applies_supported_changes(self) -> None:
        original = (
            "const API_TOKEN = \"abcd1234\";\n"
            "console.log(\"debug\");\n"
            "const title = \"TODO demo\";\n"
        )
        findings = [
            {"message": "Debug logging found (console.log)."},
            {"message": "Potential hardcoded secret detected."},
            {"message": "TODO marker found in changed code."},
        ]

        updated, applied, unresolved = GitHubOps._apply_safe_autofixes(
            path="src/app.js",
            content=original,
            findings=findings,
        )

        self.assertIn('const API_TOKEN = process.env.API_TOKEN || "";', updated)
        self.assertIn("// gitbot: removed debug log", updated)
        self.assertNotIn("TODO", updated)
        self.assertIn("TRACKED_TASK", updated)
        self.assertIn("remove_console_log", applied)
        self.assertIn("secret_to_env", applied)
        self.assertEqual(unresolved, [])

    def test_eval_finding_is_reported_as_manual(self) -> None:
        original = "value = eval(user_input)\n"
        findings = [{"message": "Use of eval detected."}]

        updated, applied, unresolved = GitHubOps._apply_safe_autofixes(
            path="app/sample.py",
            content=original,
            findings=findings,
        )

        self.assertEqual(updated, original)
        self.assertEqual(applied, [])
        self.assertTrue(any("eval" in item for item in unresolved))


if __name__ == "__main__":
    unittest.main()
