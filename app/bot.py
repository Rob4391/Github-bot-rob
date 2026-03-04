from .ai import BotIntelligence, ReviewResult
from .config import Settings
from .github_ops import GitHubOps


class GitBot:
    def __init__(self, settings: Settings, github_ops: GitHubOps, brain: BotIntelligence) -> None:
        self.settings = settings
        self.github = github_ops
        self.brain = brain

    def handle_event(self, event_name: str, payload: dict) -> list[str]:
        installation_id = self._installation_id_from_payload(payload)
        if self.settings.auth_mode == "app" and installation_id is None:
            return ["ignored: missing installation id for GitHub App auth"]

        if event_name == "pull_request":
            return self._handle_pull_request(payload, installation_id)
        if event_name == "issue_comment":
            return self._handle_issue_comment(payload, installation_id)
        return [f"ignored: unsupported event '{event_name}'"]

    def _handle_pull_request(self, payload: dict, installation_id: int | None) -> list[str]:
        action = payload.get("action", "")
        repo_name = payload["repository"]["full_name"]
        pr = payload["pull_request"]
        pr_number = int(pr["number"])
        actions: list[str] = []

        if action in {"opened", "reopened", "synchronize", "ready_for_review"} and not pr.get(
            "draft", False
        ):
            result = self._run_review(
                repo_name=repo_name, pr_number=pr_number, installation_id=installation_id
            )
            review_event = (
                "APPROVE"
                if (result.approve and result.confidence >= self.settings.auto_approve_confidence)
                else "REQUEST_CHANGES"
            )
            self.github.post_pr_review(
                repo_name=repo_name,
                number=pr_number,
                body=self._format_review(result),
                event=review_event,
                installation_id=installation_id,
            )
            actions.append(f"reviewed PR #{pr_number}: {review_event}")

        if action == "closed" and bool(pr.get("merged")):
            bump_type = self._resolve_bump_type(pr.get("labels", []))
            default_branch = payload["repository"]["default_branch"]
            old_version, new_version = self.github.bump_version_file(
                repo_name=repo_name,
                branch=default_branch,
                bump_type=bump_type,
                installation_id=installation_id,
            )
            notes = (
                f"Automated release from merged PR #{pr_number}: {pr.get('title', '').strip()}\n\n"
                f"Bump type: `{bump_type}`\n"
                f"Version: `{old_version}` -> `{new_version}`\n\n"
                f"{(pr.get('body') or '').strip()[:3000]}"
            )
            tag_name = self.github.ensure_tag_and_release(
                repo_name=repo_name,
                version=new_version,
                target_branch=default_branch,
                notes=notes,
                installation_id=installation_id,
            )
            self.github.add_issue_comment(
                repo_name=repo_name,
                issue_number=pr_number,
                body=(
                    f"Automated release completed.\n\n"
                    f"- Bump: `{bump_type}`\n"
                    f"- Version: `{old_version}` -> `{new_version}`\n"
                    f"- Tag: `{tag_name}`"
                ),
                installation_id=installation_id,
            )
            actions.append(f"release created: {tag_name}")

        return actions or [f"ignored pull_request action: {action}"]

    def _handle_issue_comment(self, payload: dict, installation_id: int | None) -> list[str]:
        action = payload.get("action")
        if action != "created":
            return [f"ignored issue_comment action: {action}"]

        issue = payload["issue"]
        if "pull_request" not in issue:
            return ["ignored issue_comment: not on pull request"]

        association = payload["comment"].get("author_association", "")
        if association not in {"OWNER", "MEMBER", "COLLABORATOR"}:
            return [f"ignored command: author association '{association}' not allowed"]

        comment_body = (payload["comment"].get("body") or "").strip()
        if not comment_body.startswith(self.settings.bot_command_prefix):
            return ["ignored issue_comment: no bot command"]

        parts = comment_body.split()
        repo_name = payload["repository"]["full_name"]
        pr_number = int(issue["number"])
        default_branch = payload["repository"]["default_branch"]
        actions: list[str] = []

        if len(parts) < 2:
            cmd = self.settings.bot_command_prefix
            self.github.add_issue_comment(
                repo_name=repo_name,
                issue_number=pr_number,
                body=(
                    "Supported commands:\n"
                    f"- `{cmd} review`\n"
                    f"- `{cmd} approve`\n"
                    f"- `{cmd} bump patch|minor|major`\n"
                    f"- `{cmd} release`"
                ),
                installation_id=installation_id,
            )
            return ["posted help comment"]

        command = parts[1].lower().strip()
        if command == "review":
            result = self._run_review(
                repo_name=repo_name, pr_number=pr_number, installation_id=installation_id
            )
            review_event = (
                "APPROVE"
                if (result.approve and result.confidence >= self.settings.auto_approve_confidence)
                else "REQUEST_CHANGES"
            )
            self.github.post_pr_review(
                repo_name=repo_name,
                number=pr_number,
                body=self._format_review(result),
                event=review_event,
                installation_id=installation_id,
            )
            actions.append(f"manual review posted: {review_event}")
        elif command == "approve":
            self.github.post_pr_review(
                repo_name=repo_name,
                number=pr_number,
                body="Manual approval command accepted.",
                event="APPROVE",
                installation_id=installation_id,
            )
            actions.append("manual approval posted")
        elif command == "bump":
            bump_type = parts[2].lower().strip() if len(parts) > 2 else "patch"
            if bump_type not in {"major", "minor", "patch"}:
                self.github.add_issue_comment(
                    repo_name=repo_name,
                    issue_number=pr_number,
                    body="Invalid bump type. Use: `major`, `minor`, or `patch`.",
                    installation_id=installation_id,
                )
                return ["invalid bump command"]
            old_version, new_version = self.github.bump_version_file(
                repo_name=repo_name,
                branch=default_branch,
                bump_type=bump_type,
                installation_id=installation_id,
            )
            self.github.add_issue_comment(
                repo_name=repo_name,
                issue_number=pr_number,
                body=f"Version bumped on `{default_branch}`: `{old_version}` -> `{new_version}`",
                installation_id=installation_id,
            )
            actions.append(f"version bumped: {old_version} -> {new_version}")
        elif command == "release":
            version = self.github.get_current_version(
                repo_name=repo_name,
                branch=default_branch,
                installation_id=installation_id,
            )
            notes = f"Manual release command from PR #{pr_number}.\n\nVersion: `{version}`"
            tag_name = self.github.ensure_tag_and_release(
                repo_name=repo_name,
                version=version,
                target_branch=default_branch,
                notes=notes,
                installation_id=installation_id,
            )
            self.github.add_issue_comment(
                repo_name=repo_name,
                issue_number=pr_number,
                body=f"Release ensured for `{tag_name}` on `{default_branch}`.",
                installation_id=installation_id,
            )
            actions.append(f"release ensured: {tag_name}")
        else:
            self.github.add_issue_comment(
                repo_name=repo_name,
                issue_number=pr_number,
                body=f"Unknown command `{command}`.",
                installation_id=installation_id,
            )
            actions.append(f"unknown command: {command}")

        return actions

    def _run_review(
        self, repo_name: str, pr_number: int, installation_id: int | None
    ) -> ReviewResult:
        _, pr = self.github.get_pull_request(repo_name, pr_number, installation_id)
        files = self.github.list_pr_files(
            repo_name=repo_name,
            number=pr_number,
            max_files=self.settings.max_pr_files,
            max_patch_chars=self.settings.max_patch_chars,
            installation_id=installation_id,
        )
        return self.brain.review_pull_request(
            title=pr.title or "",
            description=pr.body or "",
            changed_files=files,
        )

    @staticmethod
    def _installation_id_from_payload(payload: dict) -> int | None:
        installation = payload.get("installation")
        if not installation:
            return None
        installation_id = installation.get("id")
        if installation_id is None:
            return None
        return int(installation_id)

    @staticmethod
    def _resolve_bump_type(labels: list[dict]) -> str:
        names = {str(item.get("name", "")).lower() for item in labels}
        if "major" in names:
            return "major"
        if "minor" in names:
            return "minor"
        return "patch"

    @staticmethod
    def _format_review(result: ReviewResult) -> str:
        lines = [
            "## GitBot Review",
            f"- Source: `{result.source}`",
            f"- Confidence: `{result.confidence:.2f}`",
            "",
            result.summary.strip(),
        ]
        if result.findings:
            lines.append("")
            lines.append("### Findings")
            lines.extend(f"- {item}" for item in result.findings)
        else:
            lines.append("")
            lines.append("No critical findings detected.")
        return "\n".join(lines)
