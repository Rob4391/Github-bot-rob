import re

RELEASE_NON_CODE_PREFIXES = (
    ".github/",
    ".gitbot/",
    "docs/",
    "doc/",
    "tests/",
    "test/",
)
RELEASE_TRIGGER_FILES = {
    "version",
    "dockerfile",
    "requirements.txt",
    "pyproject.toml",
    "poetry.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "go.mod",
    "go.sum",
    "cargo.toml",
    "cargo.lock",
}
RELEASE_TRIGGER_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".java",
    ".kt",
    ".rb",
    ".rs",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".php",
    ".swift",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".ini",
}


def should_create_release(changed_files: list[str]) -> bool:
    if not changed_files:
        return True
    for raw in changed_files:
        path = str(raw or "").strip()
        if not path:
            continue
        normalized = path.lower()
        if normalized in RELEASE_TRIGGER_FILES:
            return True
        if normalized in {"readme.md", "changelog.md", "license", "license.md"}:
            continue
        if normalized.endswith(".md") or normalized.endswith(".rst") or normalized.endswith(".txt"):
            continue
        if any(normalized.startswith(prefix) for prefix in RELEASE_NON_CODE_PREFIXES):
            continue
        for ext in RELEASE_TRIGGER_EXTENSIONS:
            if normalized.endswith(ext):
                return True
        # Non-prefixed content (for example root-level configs/scripts) should trigger release.
        return True
    return False


def resolve_bump_type(
    labels: list[dict],
    title: str,
    description: str,
    changed_files: list[str] | None = None,
) -> str:
    names = {str(item.get("name", "")).lower() for item in labels}
    combined_text = f"{title}\n{description}".lower()
    changed = [str(item or "").lower() for item in (changed_files or [])]

    if "major" in names:
        return "major"
    if "minor" in names:
        return "minor"
    if "patch" in names:
        return "patch"

    if "breaking-change" in names or re.search(r"\bbreaking(\s+change)?\b", combined_text):
        return "major"
    if "!" in title and re.search(r"^[a-z]+!:", title.strip().lower()):
        return "major"
    if re.search(r"\bfeat(\([^)]+\))?:", combined_text):
        return "minor"
    if "feature" in names or "enhancement" in names:
        return "minor"
    if re.search(r"\bfix(\([^)]+\))?:", combined_text):
        return "patch"
    if "bug" in names or "fix" in names or "hotfix" in names:
        return "patch"

    if any(item.endswith("version") for item in changed):
        return "patch"
    if any(
        item.endswith("requirements.txt")
        or item.endswith("pyproject.toml")
        or item.endswith("package.json")
        or item.endswith("poetry.lock")
        or item.endswith("package-lock.json")
        or item.endswith("pnpm-lock.yaml")
        for item in changed
    ):
        return "patch"
    return "patch"
