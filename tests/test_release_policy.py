import unittest

from app.release_policy import resolve_bump_type, should_create_release


class TestReleasePolicy(unittest.TestCase):
    def test_should_skip_release_for_docs_only_changes(self) -> None:
        changed = ["README.md", "docs/setup.md", "tests/test_bot.py", ".github/workflows/ci.yml"]
        self.assertFalse(should_create_release(changed))

    def test_should_release_for_code_changes(self) -> None:
        changed = ["app/main.py", "README.md"]
        self.assertTrue(should_create_release(changed))

    def test_should_release_for_dependency_manifest_changes(self) -> None:
        changed = ["requirements.txt"]
        self.assertTrue(should_create_release(changed))

    def test_resolve_bump_type_major_from_label(self) -> None:
        bump = resolve_bump_type(
            labels=[{"name": "major"}],
            title="feat: api update",
            description="",
            changed_files=["app/main.py"],
        )
        self.assertEqual(bump, "major")

    def test_resolve_bump_type_major_from_breaking_text(self) -> None:
        bump = resolve_bump_type(
            labels=[],
            title="feat!: auth update",
            description="breaking change in token format",
            changed_files=["app/auth.py"],
        )
        self.assertEqual(bump, "major")

    def test_resolve_bump_type_minor_from_feat(self) -> None:
        bump = resolve_bump_type(
            labels=[],
            title="feat: add webhook retries",
            description="",
            changed_files=["app/main.py"],
        )
        self.assertEqual(bump, "minor")

    def test_resolve_bump_type_patch_by_default(self) -> None:
        bump = resolve_bump_type(
            labels=[],
            title="chore: tidy docs",
            description="",
            changed_files=["requirements.txt"],
        )
        self.assertEqual(bump, "patch")


if __name__ == "__main__":
    unittest.main()
