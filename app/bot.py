try:
    from github.GithubException import GithubException
except Exception:  # pragma: no cover - fallback for lightweight local test envs
    class GithubException(Exception):
        data: dict | None = None

from .ai import ALLOWED_CHECKS, BotIntelligence, Finding, ReviewResult
from .config import Settings
from .github_ops import GitHubOps
from .release_policy import resolve_bump_type, should_create_release
from .state import BotState


class GitBot:
    def __init__(
        self,
        settings: Settings,
        github_ops: GitHubOps,
        brain: BotIntelligence,
        state: BotState,
    ) -> None:
        self.settings = settings
        self.github = github_ops
        self.brain = brain
        self.state = state

    def handle_event(self, event_name: str, payload: dict) -> list[str]:
        installation_id = self._installation_id_from_payload(payload)
        if self.settings.auth_mode == "app" and installation_id is None:
            return ["ignored: missing installation id for GitHub App auth"]

        if event_name == "pull_request":
            return self._handle_pull_request(payload, installation_id)
        if event_name == "issue_comment":
            return self._handle_issue_comment(payload, installation_id)
        if event_name == "check_run":
            return self._handle_check_run(payload, installation_id)
        return [f"ignored: unsupported event '{event_name}'"]

    def _handle_pull_request(self, payload: dict, installation_id: int | None) -> list[str]:
        action = payload.get("action", "")
        repo_name = payload["repository"]["full_name"]
        pr = payload["pull_request"]
        pr_number = int(pr["number"])
        default_branch = payload["repository"]["default_branch"]
        actions: list[str] = []

        if action in {"opened", "reopened", "synchronize", "ready_for_review"} and not pr.get(
            "draft", False
        ):
            if not self.settings.auto_fullcheck_on_pr_events:
                actions.append("auto fullcheck skipped: AUTO_FULLCHECK_ON_PR_EVENTS=false")
            else:
                result, check_url = self._run_fullcheck(
                    repo_name=repo_name,
                    pr_number=pr_number,
                    installation_id=installation_id,
                    selected_checks=set(ALLOWED_CHECKS),
                )
                self._safe_issue_comment(
                    repo_name=repo_name,
                    installation_id=installation_id,
                    issue_number=pr_number,
                    body=self._format_fullcheck_summary(result, check_url),
                )

                review_event = self._review_event_for_result(
                    repo_name=repo_name,
                    pr_number=pr_number,
                    installation_id=installation_id,
                    result=result,
                )
                body = self._format_review(result)
                self.github.post_pr_review(
                    repo_name=repo_name,
                    number=pr_number,
                    body=body,
                    event=review_event,
                    installation_id=installation_id,
                )
                actions.append(f"fullcheck+review posted for PR #{pr_number}: {review_event}")

                self._post_meme(
                    repo_name=repo_name,
                    pr_number=pr_number,
                    installation_id=installation_id,
                    bucket=self._meme_bucket(result.risk_score, ok=result.approve),
                    reason="Auto fullcheck complete",
                )

        if action == "closed" and bool(pr.get("merged")):
            changed = self.github.list_pr_files(
                repo_name=repo_name,
                number=pr_number,
                max_files=0,
                max_patch_chars=0,
                installation_id=installation_id,
            )
            changed_paths = [item.filename for item in changed]
            if not should_create_release(changed_paths):
                self.github.add_issue_comment(
                    repo_name=repo_name,
                    issue_number=pr_number,
                    body=(
                        "Automated release skipped (no release-worthy code/dependency changes detected)."
                    ),
                    installation_id=installation_id,
                )
                actions.append("release skipped: no release-worthy changes")
                return actions

            bump_type = resolve_bump_type(
                labels=pr.get("labels", []),
                title=str(pr.get("title", "") or ""),
                description=str(pr.get("body", "") or ""),
                changed_files=changed_paths,
            )
            try:
                old_version, new_version = self.github.bump_version_file(
                    repo_name=repo_name,
                    branch=default_branch,
                    bump_type=bump_type,
                    installation_id=installation_id,
                )
                changelog = self._build_changelog(
                    repo_name=repo_name,
                    base_branch=default_branch,
                    installation_id=installation_id,
                )
                notes = (
                    f"Automated release from merged PR #{pr_number}: {pr.get('title', '').strip()}\n\n"
                    f"Bump type: `{bump_type}`\n"
                    f"Version: `{old_version}` -> `{new_version}`\n\n"
                    f"{changelog}\n\n"
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
                self._post_final_meme(
                    repo_name=repo_name,
                    pr_number=pr_number,
                    installation_id=installation_id,
                    reason="Automated release completed",
                    bucket="good",
                    failed=False,
                )
            except GithubException as exc:
                message = self._format_github_exception(exc)
                self._safe_issue_comment(
                    repo_name=repo_name,
                    issue_number=pr_number,
                    installation_id=installation_id,
                    body=f"Automated release failed: {message}",
                )
                actions.append(f"release failed: {message}")
                self._post_final_meme(
                    repo_name=repo_name,
                    pr_number=pr_number,
                    installation_id=installation_id,
                    reason="Automated release failed",
                    bucket="fail",
                    failed=True,
                )

        return actions or [f"ignored pull_request action: {action}"]

    def _handle_issue_comment(self, payload: dict, installation_id: int | None) -> list[str]:
        action = payload.get("action")
        if action != "created":
            return [f"ignored issue_comment action: {action}"]

        issue = payload["issue"]
        if "pull_request" not in issue:
            return ["ignored issue_comment: not on pull request"]

        association = payload["comment"].get("author_association", "")
        if association not in {
            "OWNER",
            "MEMBER",
            "COLLABORATOR",
            "CONTRIBUTOR",
            "FIRST_TIMER",
            "FIRST_TIME_CONTRIBUTOR",
        }:
            return [f"ignored command: author association '{association}' not allowed"]

        comment_body = (payload["comment"].get("body") or "").strip()
        if not comment_body.startswith(self.settings.bot_command_prefix):
            return ["ignored issue_comment: no bot command"]

        parts = comment_body.split()
        repo_name = payload["repository"]["full_name"]
        pr_number = int(issue["number"])
        default_branch = payload["repository"]["default_branch"]
        comment_id = int(payload["comment"]["id"])
        self._react_to_comment(repo_name, comment_id, "eyes", installation_id)

        if len(parts) < 2:
            self._safe_issue_comment(
                repo_name=repo_name,
                issue_number=pr_number,
                installation_id=installation_id,
                body=self._help_text(),
            )
            self._react_to_comment(repo_name, comment_id, "+1", installation_id)
            return ["posted help comment"]

        command = parts[1].lower().strip()
        args = parts[2:]
        meme_bucket: str | None = None
        meme_reason = f"Command `{command}` completed"
        meme_failed = False
        meme_explicit_request = False
        try:
            if command == "review":
                profile = self._profile_for_repo(repo_name)
                result = self._run_review(
                    repo_name=repo_name,
                    pr_number=pr_number,
                    installation_id=installation_id,
                    profile=profile,
                )
                self._cache_review(repo_name, pr_number, result)
                review_event = self._review_event_for_result(
                    repo_name=repo_name,
                    pr_number=pr_number,
                    installation_id=installation_id,
                    result=result,
                )
                self.github.post_pr_review(
                    repo_name=repo_name,
                    number=pr_number,
                    body=self._format_review(result),
                    event=review_event,
                    installation_id=installation_id,
                )
                meme_bucket = self._meme_bucket(result.risk_score, ok=result.approve)
                meme_reason = "Manual review complete"
                self._react_to_comment(repo_name, comment_id, "rocket", installation_id)
                return [f"manual review posted: {review_event}"]

            if command == "fullcheck":
                result, check_url = self._run_fullcheck(
                    repo_name=repo_name,
                    pr_number=pr_number,
                    installation_id=installation_id,
                    selected_checks=set(ALLOWED_CHECKS),
                )
                summary = self._format_fullcheck_summary(result, check_url)
                self._safe_issue_comment(
                    repo_name=repo_name,
                    issue_number=pr_number,
                    installation_id=installation_id,
                    body=summary,
                )
                meme_bucket = self._meme_bucket(result.risk_score, ok=result.approve)
                meme_reason = "Fullcheck complete"
                self._react_to_comment(repo_name, comment_id, "hooray", installation_id)
                return [f"fullcheck completed: {check_url}"]

            if command == "check":
                check_name = args[0].lower().strip() if args else ""
                if check_name in {"all", ""}:
                    result, check_url = self._run_fullcheck(
                        repo_name=repo_name,
                        pr_number=pr_number,
                        installation_id=installation_id,
                        selected_checks=set(ALLOWED_CHECKS),
                    )
                    self._safe_issue_comment(
                        repo_name=repo_name,
                        issue_number=pr_number,
                        installation_id=installation_id,
                        body=self._format_fullcheck_summary(result, check_url),
                    )
                    meme_bucket = self._meme_bucket(result.risk_score, ok=result.approve)
                    meme_reason = "Check all complete"
                    self._react_to_comment(repo_name, comment_id, "rocket", installation_id)
                    return [f"full check posted: {check_url}"]

                if check_name not in ALLOWED_CHECKS:
                    self._safe_issue_comment(
                        repo_name=repo_name,
                        issue_number=pr_number,
                        installation_id=installation_id,
                        body=(
                            "Invalid check. Use one of: "
                            + ", ".join(sorted(ALLOWED_CHECKS))
                            + ", or `all`."
                        ),
                    )
                    meme_failed = True
                    meme_reason = "Check command invalid"
                    self._react_to_comment(repo_name, comment_id, "confused", installation_id)
                    return ["invalid check command"]

                profile = self._profile_for_repo(repo_name)
                result = self._run_review(
                    repo_name=repo_name,
                    pr_number=pr_number,
                    installation_id=installation_id,
                    profile=profile,
                    enabled_checks={check_name},
                )
                self._cache_review(repo_name, pr_number, result)
                self.github.post_pr_review(
                    repo_name=repo_name,
                    number=pr_number,
                    body=self._format_review(result),
                    event="COMMENT",
                    installation_id=installation_id,
                )
                meme_bucket = self._meme_bucket(result.risk_score, ok=result.approve)
                meme_reason = f"Selective check `{check_name}` complete"
                self._react_to_comment(repo_name, comment_id, "+1", installation_id)
                return [f"selective check posted: {check_name}"]

            if command == "approve":
                if self.settings.require_green_checks_for_approve:
                    checks_ok, details = self.github.checks_green(
                        repo_name=repo_name,
                        number=pr_number,
                        installation_id=installation_id,
                    )
                    if not checks_ok:
                        self._safe_issue_comment(
                            repo_name=repo_name,
                            issue_number=pr_number,
                            installation_id=installation_id,
                            body=(
                                "Approval blocked by CI gate.\n\n"
                                "Some status checks are not green:\n"
                                + "\n".join(f"- {item}" for item in details[:15])
                            ),
                        )
                        meme_failed = True
                        meme_reason = "Approval blocked by CI"
                        self._react_to_comment(repo_name, comment_id, "eyes", installation_id)
                        return ["approval blocked by ci gate"]
                self.github.post_pr_review(
                    repo_name=repo_name,
                    number=pr_number,
                    body="Manual approval command accepted.",
                    event="APPROVE",
                    installation_id=installation_id,
                )
                meme_reason = "Approval posted"
                self._react_to_comment(repo_name, comment_id, "rocket", installation_id)
                return ["manual approval posted"]

            if command == "merge":
                merge_method = self._resolve_merge_method(args)
                if merge_method is None:
                    self._safe_issue_comment(
                        repo_name=repo_name,
                        issue_number=pr_number,
                        installation_id=installation_id,
                        body="Invalid merge method. Use: `squash`, `merge`, or `rebase`.",
                    )
                    meme_failed = True
                    meme_reason = "Merge command invalid"
                    self._react_to_comment(repo_name, comment_id, "confused", installation_id)
                    return ["invalid merge command"]
                merge_out = self._execute_guarded_merge(
                    repo_name=repo_name,
                    pr_number=pr_number,
                    installation_id=installation_id,
                    merge_method=merge_method,
                )
                self._safe_issue_comment(
                    repo_name=repo_name,
                    issue_number=pr_number,
                    installation_id=installation_id,
                    body=str(merge_out["body"]),
                )
                status = str(merge_out["status"])
                if status == "merged":
                    meme_reason = f"PR merged with `{merge_method}`"
                    self._react_to_comment(repo_name, comment_id, "rocket", installation_id)
                elif status == "blocked":
                    meme_failed = True
                    meme_reason = "Merge blocked by guardrails"
                    self._react_to_comment(repo_name, comment_id, "eyes", installation_id)
                else:
                    meme_failed = True
                    meme_reason = "Merge rejected by GitHub"
                    self._react_to_comment(repo_name, comment_id, "confused", installation_id)
                    return [str(merge_out["result"])]

            if command == "close":
                state_out = self.github.set_pull_request_state(
                    repo_name=repo_name,
                    number=pr_number,
                    state="closed",
                    installation_id=installation_id,
                )
                changed = bool(state_out.get("changed", False))
                message = str(state_out.get("message", "Pull request close requested."))
                self._safe_issue_comment(
                    repo_name=repo_name,
                    issue_number=pr_number,
                    installation_id=installation_id,
                    body=message,
                )
                if changed:
                    meme_reason = "PR closed by command"
                    self._react_to_comment(repo_name, comment_id, "rocket", installation_id)
                    return ["pull request closed"]
                meme_failed = True
                meme_reason = "PR close no-op"
                self._react_to_comment(repo_name, comment_id, "eyes", installation_id)
                return ["pull request close no-op"]

            if command == "reopen":
                state_out = self.github.set_pull_request_state(
                    repo_name=repo_name,
                    number=pr_number,
                    state="open",
                    installation_id=installation_id,
                )
                changed = bool(state_out.get("changed", False))
                message = str(state_out.get("message", "Pull request reopen requested."))
                self._safe_issue_comment(
                    repo_name=repo_name,
                    issue_number=pr_number,
                    installation_id=installation_id,
                    body=message,
                )
                if changed:
                    meme_reason = "PR reopened by command"
                    self._react_to_comment(repo_name, comment_id, "rocket", installation_id)
                    return ["pull request reopened"]
                meme_failed = True
                meme_reason = "PR reopen blocked or no-op"
                self._react_to_comment(repo_name, comment_id, "eyes", installation_id)
                return ["pull request reopen no-op"]

            if command == "bump":
                bump_type = args[0].lower().strip() if args else "patch"
                if bump_type not in {"major", "minor", "patch"}:
                    self._safe_issue_comment(
                        repo_name=repo_name,
                        issue_number=pr_number,
                        installation_id=installation_id,
                        body="Invalid bump type. Use: `major`, `minor`, or `patch`.",
                    )
                    meme_failed = True
                    meme_reason = "Version bump command invalid"
                    self._react_to_comment(repo_name, comment_id, "confused", installation_id)
                    return ["invalid bump command"]
                old_version, new_version = self.github.bump_version_file(
                    repo_name=repo_name,
                    branch=default_branch,
                    bump_type=bump_type,
                    installation_id=installation_id,
                )
                self._safe_issue_comment(
                    repo_name=repo_name,
                    issue_number=pr_number,
                    installation_id=installation_id,
                    body=f"Version bumped on `{default_branch}`: `{old_version}` -> `{new_version}`",
                )
                meme_reason = f"Version bumped to `{new_version}`"
                self._react_to_comment(repo_name, comment_id, "hooray", installation_id)
                return [f"version bumped: {old_version} -> {new_version}"]

            if command == "release":
                version = self.github.get_current_version(
                    repo_name=repo_name,
                    branch=default_branch,
                    installation_id=installation_id,
                )
                changelog = self._build_changelog(
                    repo_name=repo_name,
                    base_branch=default_branch,
                    installation_id=installation_id,
                )
                notes = (
                    f"Manual release command from PR #{pr_number}.\n\n"
                    f"Version: `{version}`\n\n{changelog}"
                )
                tag_name = self.github.ensure_tag_and_release(
                    repo_name=repo_name,
                    version=version,
                    target_branch=default_branch,
                    notes=notes,
                    installation_id=installation_id,
                )
                self._safe_issue_comment(
                    repo_name=repo_name,
                    issue_number=pr_number,
                    installation_id=installation_id,
                    body=f"Release ensured for `{tag_name}` on `{default_branch}`.",
                )
                meme_reason = f"Release `{tag_name}` ensured"
                self._react_to_comment(repo_name, comment_id, "rocket", installation_id)
                return [f"release ensured: {tag_name}"]

            if command == "profile":
                target = args[0].lower().strip() if args else ""
                if target not in {"strict", "balanced", "fast"}:
                    self._safe_issue_comment(
                        repo_name=repo_name,
                        issue_number=pr_number,
                        installation_id=installation_id,
                        body="Invalid profile. Use: `strict`, `balanced`, or `fast`.",
                    )
                    meme_failed = True
                    meme_reason = "Profile command invalid"
                    self._react_to_comment(repo_name, comment_id, "confused", installation_id)
                    return ["invalid profile command"]
                self.state.set_profile(repo_name, target)
                self._safe_issue_comment(
                    repo_name=repo_name,
                    issue_number=pr_number,
                    installation_id=installation_id,
                    body=f"Review profile for `{repo_name}` set to `{target}`.",
                )
                meme_reason = f"Profile set to `{target}`"
                self._react_to_comment(repo_name, comment_id, "+1", installation_id)
                return [f"profile set: {target}"]

            if command == "meme":
                target = args[0].lower().strip() if args else ""
                if target not in {"", "now"}:
                    self._safe_issue_comment(
                        repo_name=repo_name,
                        issue_number=pr_number,
                        installation_id=installation_id,
                        body="Meme mode is always ON. Use `/gitbot meme now`.",
                    )
                    meme_failed = True
                    meme_reason = "Meme command invalid"
                    self._react_to_comment(repo_name, comment_id, "confused", installation_id)
                    return ["invalid meme command"]

                last_review = self.state.get_last_review(repo_name, pr_number)
                risk = int((last_review or {}).get("risk_score", 50))
                bucket = self._meme_bucket(risk_score=risk, ok=risk < self.settings.high_risk_threshold)
                meme_bucket = bucket
                meme_reason = "Requested meme"
                meme_explicit_request = True
                self._react_to_comment(repo_name, comment_id, "laugh", installation_id)
                return ["meme posted"]

            if command == "suggest":
                use_cached = bool(args and args[0].lower().strip() in {"cache", "cached"})
                suggestions: list[str] = []
                if use_cached:
                    last_review = self.state.get_last_review(repo_name, pr_number)
                    if last_review:
                        suggestions = self._normalize_suggestions(
                            list(last_review.get("suggestions", []))
                        )
                else:
                    profile = self._profile_for_repo(repo_name)
                    result = self._run_review(
                        repo_name=repo_name,
                        pr_number=pr_number,
                        installation_id=installation_id,
                        profile=profile,
                        enabled_checks=set(ALLOWED_CHECKS),
                    )
                    self._cache_review(repo_name, pr_number, result)
                    suggestions = self._suggestions_from_result(result)
                    if not suggestions and result.source == "llm-unavailable":
                        cached_review = self.state.get_last_review(repo_name, pr_number)
                        if cached_review:
                            suggestions = self._normalize_suggestions(
                                list(cached_review.get("suggestions", []))
                            )
                if not suggestions:
                    self._safe_issue_comment(
                        repo_name=repo_name,
                        issue_number=pr_number,
                        installation_id=installation_id,
                        body=(
                            "No concrete PR-content suggestions are available yet.\n\n"
                            "Run `/gitbot review` once more after pushing updates, "
                            "or use `/gitbot suggest cached` to view cached items."
                        ),
                    )
                    meme_reason = "Suggest found no items"
                    self._react_to_comment(repo_name, comment_id, "eyes", installation_id)
                    return ["suggest no-op"]
                body = "## Suggested Fixes\n" + "\n".join(
                    f"- {item}" for item in suggestions[:20]
                )
                self._safe_issue_comment(
                    repo_name=repo_name,
                    issue_number=pr_number,
                    installation_id=installation_id,
                    body=body,
                )
                meme_reason = "Suggestions posted"
                self._react_to_comment(repo_name, comment_id, "+1", installation_id)
                return ["suggestions posted"]

            if command == "explain":
                finding_id = args[0].strip() if args else ""
                if not finding_id:
                    self._safe_issue_comment(
                        repo_name=repo_name,
                        issue_number=pr_number,
                        installation_id=installation_id,
                        body="Usage: `/gitbot explain <finding-id>` (example: `F2`).",
                    )
                    meme_failed = True
                    meme_reason = "Explain command invalid"
                    self._react_to_comment(repo_name, comment_id, "confused", installation_id)
                    return ["explain missing id"]
                finding = self._finding_by_id(repo_name, pr_number, finding_id)
                if not finding:
                    self._safe_issue_comment(
                        repo_name=repo_name,
                        issue_number=pr_number,
                        installation_id=installation_id,
                        body=f"Finding `{finding_id}` not found. Run `/gitbot review` again.",
                    )
                    meme_failed = True
                    meme_reason = "Explain finding not found"
                    self._react_to_comment(repo_name, comment_id, "confused", installation_id)
                    return [f"explain not found: {finding_id}"]
                self._safe_issue_comment(
                    repo_name=repo_name,
                    issue_number=pr_number,
                    installation_id=installation_id,
                    body=(
                        f"### Finding {finding.id}\n"
                        f"- Severity: `{finding.severity}`\n"
                        f"- Category: `{finding.category}`\n"
                        f"- File: `{finding.file}`\n"
                        f"- Message: {finding.message}\n"
                        f"- Suggestion: {finding.suggestion or 'n/a'}"
                    ),
                )
                meme_reason = f"Explained `{finding.id}`"
                self._react_to_comment(repo_name, comment_id, "eyes", installation_id)
                return [f"explain posted: {finding_id}"]

            if command == "remember":
                note = " ".join(args).strip()
                if not note:
                    self._safe_issue_comment(
                        repo_name=repo_name,
                        issue_number=pr_number,
                        installation_id=installation_id,
                        body="Usage: `/gitbot remember <note>`.",
                    )
                    meme_failed = True
                    meme_reason = "Remember command invalid"
                    self._react_to_comment(repo_name, comment_id, "confused", installation_id)
                    return ["remember missing note"]
                self.state.add_memory(repo_name, pr_number, note)
                self._safe_issue_comment(
                    repo_name=repo_name,
                    issue_number=pr_number,
                    installation_id=installation_id,
                    body=f"Memory note saved for PR #{pr_number}.",
                )
                meme_reason = "Memory note saved"
                self._react_to_comment(repo_name, comment_id, "+1", installation_id)
                return ["memory saved"]

            if command == "memory":
                notes = self.state.get_memory(repo_name, pr_number)
                if not notes:
                    body = "No memory notes for this PR yet."
                else:
                    body = "## PR Memory\n" + "\n".join(
                        f"- {idx}. {item}" for idx, item in enumerate(notes, start=1)
                    )
                self._safe_issue_comment(
                    repo_name=repo_name,
                    issue_number=pr_number,
                    installation_id=installation_id,
                    body=body,
                )
                meme_reason = "Memory posted"
                self._react_to_comment(repo_name, comment_id, "eyes", installation_id)
                return ["memory posted"]

            if command == "autofix":
                last_review = self.state.get_last_review(repo_name, pr_number)
                findings = list((last_review or {}).get("findings", []))
                autofix_result = self.github.apply_autofix_to_pr_branch(
                    repo_name=repo_name,
                    source_pr_number=pr_number,
                    findings=findings,
                    installation_id=installation_id,
                )
                branch = autofix_result.get("branch", default_branch)
                commit_url = str(autofix_result.get("commit_url", "") or "")
                changed_count = int(autofix_result.get("changed_count", 0) or 0)
                changed_files = [str(item) for item in autofix_result.get("changed_files", [])]
                unresolved = [str(item) for item in autofix_result.get("unresolved", [])]
                commit_line = f"\n- Last commit: {commit_url}" if commit_url else ""
                changed_lines = ""
                if changed_files:
                    changed_lines = "\n- Files updated:\n" + "\n".join(
                        f"  - `{path}`" for path in changed_files[:8]
                    )
                unresolved_lines = ""
                if unresolved:
                    unresolved_lines = "\n- Remaining manual items:\n" + "\n".join(
                        f"  - {item}" for item in unresolved[:6]
                    )
                if changed_count > 0:
                    intro = "Autofix applied safe code changes on this PR branch."
                else:
                    intro = "Autofix found no safe code changes to apply."
                self._safe_issue_comment(
                    repo_name=repo_name,
                    issue_number=pr_number,
                    installation_id=installation_id,
                    body=(
                        f"{intro}\n\n"
                        f"- Branch: `{branch}`\n"
                        f"- Files changed: `{changed_count}`{commit_line}{changed_lines}"
                        f"{unresolved_lines}\n"
                        "- Next: run `/gitbot fullcheck` again."
                    ),
                )
                if changed_count == 0:
                    meme_failed = True
                    meme_reason = "Autofix found no safe changes"
                else:
                    meme_reason = "Autofix applied updates"
                reaction = "rocket" if changed_count > 0 else "eyes"
                self._react_to_comment(repo_name, comment_id, reaction, installation_id)
                return [f"autofix committed on branch: {branch}"]

            self._safe_issue_comment(
                repo_name=repo_name,
                issue_number=pr_number,
                installation_id=installation_id,
                body=f"Unknown command `{command}`.\n\n{self._help_text()}",
            )
            meme_failed = True
            meme_reason = f"Unknown command `{command}`"
            self._react_to_comment(repo_name, comment_id, "confused", installation_id)
            return [f"unknown command: {command}"]
        except GithubException as exc:
            message = self._format_github_exception(exc)
            self._safe_issue_comment(
                repo_name=repo_name,
                issue_number=pr_number,
                installation_id=installation_id,
                body=f"Command `{command}` failed: {message}",
            )
            meme_failed = True
            meme_reason = f"Command `{command}` failed"
            self._react_to_comment(repo_name, comment_id, "confused", installation_id)
            return [f"command failed: {command}: {message}"]
        finally:
            self._post_final_meme(
                repo_name=repo_name,
                pr_number=pr_number,
                installation_id=installation_id,
                reason=meme_reason,
                bucket=meme_bucket,
                failed=meme_failed,
                explicit_request=meme_explicit_request,
            )

    def _handle_check_run(self, payload: dict, installation_id: int | None) -> list[str]:
        if payload.get("action") != "requested_action":
            return [f"ignored check_run action: {payload.get('action')}"]

        requested = payload.get("requested_action", {})
        identifier = str(requested.get("identifier", "")).strip()
        repo_name = payload["repository"]["full_name"]
        pr_number = self._pr_number_from_check_run_payload(payload)
        if pr_number is None:
            return ["ignored check_run requested_action: no linked PR number"]

        _, pr = self.github.get_pull_request(repo_name, pr_number, installation_id)
        base_branch = pr.base.ref

        if identifier == "autofix":
            last_review = self.state.get_last_review(repo_name, pr_number)
            findings = list((last_review or {}).get("findings", []))
            autofix_result = self.github.apply_autofix_to_pr_branch(
                repo_name=repo_name,
                source_pr_number=pr_number,
                findings=findings,
                installation_id=installation_id,
            )
            branch = autofix_result.get("branch", base_branch)
            commit_url = str(autofix_result.get("commit_url", "") or "")
            changed_count = int(autofix_result.get("changed_count", 0) or 0)
            changed_files = [str(item) for item in autofix_result.get("changed_files", [])]
            unresolved = [str(item) for item in autofix_result.get("unresolved", [])]
            commit_line = f"\n- Last commit: {commit_url}" if commit_url else ""
            changed_lines = ""
            if changed_files:
                changed_lines = "\n- Files updated:\n" + "\n".join(
                    f"  - `{path}`" for path in changed_files[:8]
                )
            unresolved_lines = ""
            if unresolved:
                unresolved_lines = "\n- Remaining manual items:\n" + "\n".join(
                    f"  - {item}" for item in unresolved[:6]
                )
            if changed_count > 0:
                intro = "Autofix action applied safe code changes from check-run button."
            else:
                intro = "Autofix action found no safe code changes to apply."
            self._safe_issue_comment(
                repo_name=repo_name,
                issue_number=pr_number,
                installation_id=installation_id,
                body=(
                    f"{intro}\n\n"
                    f"- Branch updated: `{branch}`\n"
                    f"- Files changed: `{changed_count}`{commit_line}{changed_lines}"
                    f"{unresolved_lines}\n"
                    "- Next: run `/gitbot fullcheck` again."
                ),
            )
            self._post_final_meme(
                repo_name=repo_name,
                pr_number=pr_number,
                installation_id=installation_id,
                reason="Check-run autofix complete",
                failed=changed_count == 0,
            )
            return [f"check_run action executed: autofix on branch {branch}"]

        if identifier == "merge-pr":
            merge_out = self._execute_guarded_merge(
                repo_name=repo_name,
                pr_number=pr_number,
                installation_id=installation_id,
                merge_method="squash",
            )
            status = str(merge_out["status"])
            self._safe_issue_comment(
                repo_name=repo_name,
                issue_number=pr_number,
                installation_id=installation_id,
                body=str(merge_out["body"]),
            )
            self._post_final_meme(
                repo_name=repo_name,
                pr_number=pr_number,
                installation_id=installation_id,
                reason="Check-run merge action completed",
                failed=status != "merged",
            )
            return [f"check_run action executed: {merge_out['result']}"]

        if identifier == "rerun-fullcheck":
            result, check_url = self._run_fullcheck(
                repo_name=repo_name,
                pr_number=pr_number,
                installation_id=installation_id,
                selected_checks=set(ALLOWED_CHECKS),
            )
            self._safe_issue_comment(
                repo_name=repo_name,
                issue_number=pr_number,
                installation_id=installation_id,
                body=self._format_fullcheck_summary(result, check_url),
            )
            self._post_final_meme(
                repo_name=repo_name,
                pr_number=pr_number,
                installation_id=installation_id,
                reason="Check-run rerun fullcheck complete",
                bucket=self._meme_bucket(result.risk_score, ok=result.approve),
                failed=not result.approve,
            )
            return [f"check_run action executed: rerun fullcheck -> {check_url}"]

        return [f"ignored check_run requested_action: {identifier}"]

    def _run_fullcheck(
        self,
        repo_name: str,
        pr_number: int,
        installation_id: int | None,
        selected_checks: set[str],
    ) -> tuple[ReviewResult, str]:
        profile = self._profile_for_repo(repo_name)
        result = self._run_review(
            repo_name=repo_name,
            pr_number=pr_number,
            installation_id=installation_id,
            profile=profile,
            enabled_checks=selected_checks,
        )
        self._cache_review(repo_name, pr_number, result)
        repo, pr = self.github.get_pull_request(repo_name, pr_number, installation_id)
        self._apply_auto_labels(
            repo_name=repo_name,
            issue_number=pr_number,
            title=pr.title or "",
            description=pr.body or "",
            result=result,
            installation_id=installation_id,
        )
        details = self._format_review(result)
        check_url = self.github.create_check_run_with_actions(
            repo_name=repo_name,
            head_sha=pr.head.sha,
            name="GitBot FullCheck",
            summary=f"Risk score: {result.risk_score}/100 | Profile: {result.profile}",
            details_text=details,
            external_id=f"{repo_name}#{pr_number}",
            actions=[
                {
                    "label": "Autofix",
                    "description": "Apply safe autofix edits on PR branch",
                    "identifier": "autofix",
                },
                {
                    "label": "Rerun FullCheck",
                    "description": "Run all checks again",
                    "identifier": "rerun-fullcheck",
                },
                {
                    "label": "Merge PR",
                    "description": "Merge with guarded squash",
                    "identifier": "merge-pr",
                },
            ],
            installation_id=installation_id,
            conclusion="success" if result.approve else "action_required",
        )
        return result, check_url

    def _run_review(
        self,
        repo_name: str,
        pr_number: int,
        installation_id: int | None,
        profile: str,
        enabled_checks: set[str] | None = None,
    ) -> ReviewResult:
        _, pr = self.github.get_pull_request(repo_name, pr_number, installation_id)
        files = self.github.list_pr_files(
            repo_name=repo_name,
            number=pr_number,
            max_files=self.settings.max_pr_files,
            max_patch_chars=self.settings.max_patch_chars,
            installation_id=installation_id,
        )
        memory_notes = self.state.get_memory(repo_name, pr_number)
        return self.brain.review_pull_request(
            title=pr.title or "",
            description=pr.body or "",
            changed_files=files,
            profile=profile,
            enabled_checks=enabled_checks,
            memory_notes=memory_notes,
        )

    def _cache_review(self, repo_name: str, pr_number: int, result: ReviewResult) -> None:
        self.state.set_last_review(
            repo_name=repo_name,
            pr_number=pr_number,
            payload={
                "summary": result.summary,
                "risk_score": result.risk_score,
                "source": result.source,
                "profile": result.profile,
                "findings": [item.to_dict() for item in result.findings],
                "suggestions": list(result.suggestions),
                "check_results": dict(result.check_results),
            },
        )

    def _finding_by_id(self, repo_name: str, pr_number: int, finding_id: str) -> Finding | None:
        payload = self.state.get_last_review(repo_name, pr_number)
        if not payload:
            return None
        for raw in payload.get("findings", []):
            item = Finding.from_dict(raw)
            if item.id.lower() == finding_id.lower():
                return item
        return None

    def _review_event_for_result(
        self,
        repo_name: str,
        pr_number: int,
        installation_id: int | None,
        result: ReviewResult,
    ) -> str:
        if not result.approve or result.confidence < self.settings.auto_approve_confidence:
            return "REQUEST_CHANGES"
        if not self.settings.require_green_checks_for_approve:
            return "APPROVE"

        checks_ok, _ = self.github.checks_green(
            repo_name=repo_name,
            number=pr_number,
            installation_id=installation_id,
        )
        return "APPROVE" if checks_ok else "COMMENT"

    def _apply_auto_labels(
        self,
        repo_name: str,
        issue_number: int,
        title: str,
        description: str,
        result: ReviewResult,
        installation_id: int | None,
    ) -> None:
        labels: list[str] = []
        title_l = title.lower()
        desc_l = (description or "").lower()
        if result.risk_score >= self.settings.high_risk_threshold:
            labels.append("high-risk")
        else:
            labels.append("low-risk")
        if not result.check_results.get("tests", True):
            labels.append("needs-tests")
        if any(item.category == "security" for item in result.findings):
            labels.append("security")
        if "fix" in title_l or "bug" in title_l:
            labels.append("bug")
        if "feat" in title_l or "feature" in title_l:
            labels.append("feature")
        if "breaking" in title_l or "breaking" in desc_l:
            labels.append("breaking-change")

        try:
            self.github.add_labels(
                repo_name=repo_name,
                issue_number=issue_number,
                labels=sorted(set(labels)),
                installation_id=installation_id,
            )
        except Exception:
            return

    def _build_changelog(
        self, repo_name: str, base_branch: str, installation_id: int | None
    ) -> str:
        recent = self.github.recent_merged_prs(
            repo_name=repo_name,
            base_branch=base_branch,
            limit=20,
            installation_id=installation_id,
        )
        sections: dict[str, list[str]] = {
            "Breaking Changes": [],
            "Features": [],
            "Fixes": [],
            "Other": [],
        }
        for item in recent:
            title = str(item.get("title", "")).strip()
            labels = {str(label).lower() for label in item.get("labels", [])}
            line = f"- #{item.get('number')}: {title}"
            if "breaking-change" in labels or "major" in labels:
                sections["Breaking Changes"].append(line)
            elif "feature" in labels or "enhancement" in labels:
                sections["Features"].append(line)
            elif "bug" in labels or "fix" in labels:
                sections["Fixes"].append(line)
            else:
                sections["Other"].append(line)

        lines = ["## Changelog"]
        for header in ["Breaking Changes", "Features", "Fixes", "Other"]:
            if not sections[header]:
                continue
            lines.append(f"### {header}")
            lines.extend(sections[header][:8])
            lines.append("")
        return "\n".join(lines).strip()

    def _profile_for_repo(self, repo_name: str) -> str:
        profile = self.state.get_profile(repo_name).lower().strip()
        if profile not in {"strict", "balanced", "fast"}:
            return "balanced"
        return profile

    def _help_text(self) -> str:
        cmd = self.settings.bot_command_prefix
        return (
            "Supported commands:\n"
            f"- `{cmd} review`\n"
            f"- `{cmd} fullcheck`\n"
            f"- `{cmd} check security|tests|docs|dependencies|size|quality|all`\n"
            f"- `{cmd} explain <finding-id>`\n"
            f"- `{cmd} suggest`\n"
            f"- `{cmd} remember <note>`\n"
            f"- `{cmd} memory`\n"
            f"- `{cmd} profile strict|balanced|fast`\n"
            f"- `{cmd} meme now`\n"
            f"- `{cmd} approve`\n"
            f"- `{cmd} merge [squash|merge|rebase]`\n"
            f"- `{cmd} close`\n"
            f"- `{cmd} reopen`\n"
            f"- `{cmd} bump patch|minor|major`\n"
            f"- `{cmd} release`\n"
            f"- `{cmd} autofix`"
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
    def _resolve_merge_method(args: list[str]) -> str | None:
        if not args:
            return "squash"
        method = args[0].lower().strip()
        if method in {"squash", "merge", "rebase"}:
            return method
        return None

    @staticmethod
    def _high_severity_findings(last_review: dict | None) -> list[Finding]:
        if not last_review:
            return []
        findings: list[Finding] = []
        for raw in last_review.get("findings", []):
            item = Finding.from_dict(raw)
            if item.severity == "high":
                findings.append(item)
        return findings

    def _execute_guarded_merge(
        self,
        repo_name: str,
        pr_number: int,
        installation_id: int | None,
        merge_method: str,
    ) -> dict[str, str]:
        if self.settings.require_green_checks_for_approve:
            checks_ok, details = self.github.checks_green(
                repo_name=repo_name,
                number=pr_number,
                installation_id=installation_id,
            )
            if not checks_ok:
                return {
                    "status": "blocked",
                    "result": "merge blocked by ci gate",
                    "body": (
                        "Merge blocked by CI gate.\n\n"
                        "Some status checks are not green:\n"
                        + "\n".join(f"- {item}" for item in details[:15])
                    ),
                }

        last_review = self.state.get_last_review(repo_name, pr_number)
        if not last_review:
            return {
                "status": "blocked",
                "result": "merge blocked: missing review context",
                "body": "No recent review context found. Run `/gitbot fullcheck` before merging.",
            }

        high_findings = self._high_severity_findings(last_review)
        if high_findings:
            return {
                "status": "blocked",
                "result": "merge blocked by high findings",
                "body": (
                    "Merge blocked by review gate.\n\n"
                    "High-severity findings are still open:\n"
                    + "\n".join(
                        f"- [{item.id}] `{item.file}`: {item.message}" for item in high_findings[:8]
                    )
                    + "\n\nRun `/gitbot autofix` and `/gitbot fullcheck` again."
                ),
            }

        merge_result = self.github.merge_pull_request(
            repo_name=repo_name,
            number=pr_number,
            merge_method=merge_method,
            installation_id=installation_id,
        )
        if bool(merge_result.get("merged", False)):
            sha = str(merge_result.get("sha", "") or "")
            sha_line = f"\n- Merge commit: `{sha[:12]}`" if sha else ""
            return {
                "status": "merged",
                "result": f"pull request merged with method: {merge_method}",
                "body": f"PR merged by GitBot.\n\n- Method: `{merge_method}`{sha_line}",
            }

        message = str(merge_result.get("message", "") or "merge rejected by GitHub")
        return {
            "status": "rejected",
            "result": f"merge rejected: {message}",
            "body": f"Merge was not completed: {message}",
        }

    @staticmethod
    def _format_review(result: ReviewResult) -> str:
        lines = [
            "## GitBot Review",
            f"- Source: `{result.source}`",
            f"- Profile: `{result.profile}`",
            f"- Risk score: `{result.risk_score}/100`",
            f"- Confidence: `{result.confidence:.2f}`",
            "",
            result.summary.strip(),
            "",
            "### Check Results",
        ]
        for name in sorted(result.check_results):
            status = "pass" if result.check_results[name] else "flagged"
            emoji = "✅" if status == "pass" else "⚠️"
            lines.append(f"- {emoji} `{name}`: {status}")
        if result.findings:
            lines.append("")
            lines.append("### Findings")
            for item in result.findings:
                lines.append(
                    f"- [{item.id}] ({item.severity}) `{item.category}` `{item.file}`: {item.message}"
                )
        else:
            lines.append("")
            lines.append("No critical findings detected.")
        return "\n".join(lines)

    @staticmethod
    def _is_infra_suggestion(item: str) -> bool:
        normalized = item.lower()
        return (
            "verify ai provider configuration and credentials" in normalized
            or ("google_cloud_project" in normalized and "vertex_model" in normalized)
        )

    def _normalize_suggestions(self, items: list[str]) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        for raw in items:
            suggestion = str(raw or "").strip()
            if not suggestion:
                continue
            if self._is_infra_suggestion(suggestion):
                continue
            key = suggestion.lower()
            if key in seen:
                continue
            seen.add(key)
            output.append(suggestion)
        return output

    def _suggestions_from_result(self, result: ReviewResult) -> list[str]:
        suggestions = self._normalize_suggestions(list(result.suggestions))
        if suggestions:
            return suggestions

        # Fallback: derive concrete action bullets from findings when model suggestions are sparse.
        fallback: list[str] = []
        for finding in result.findings:
            if finding.suggestion and not self._is_infra_suggestion(finding.suggestion):
                action = finding.suggestion.strip()
            else:
                action = f"Address {finding.category} issue: {finding.message.strip()}"
            line = f"`{finding.file}`: {action}" if finding.file and finding.file != "*" else action
            fallback.append(line)
        return self._normalize_suggestions(fallback)

    def _format_fullcheck_summary(self, result: ReviewResult, check_url: str) -> str:
        good = [name for name, ok in sorted(result.check_results.items()) if ok]
        improve = [name for name, ok in sorted(result.check_results.items()) if not ok]
        lines = [
            "## GitBot FullCheck Summary",
            f"- Profile: `{result.profile}`",
            f"- Risk score: `{result.risk_score}/100`",
            f"- Check run: {check_url}",
            "",
            "### Good State ✅",
        ]
        if good:
            lines.extend(f"- `{name}` is healthy" for name in good)
        else:
            lines.append("- No checks passed yet.")

        lines.append("")
        lines.append("### Needs Improvement ⚠️")
        if improve:
            lines.extend(f"- `{name}` needs attention" for name in improve)
        else:
            lines.append("- No blocked checks.")

        if result.findings:
            lines.append("")
            lines.append("### Top Findings")
            for finding in result.findings[:8]:
                lines.append(
                    f"- [{finding.id}] ({finding.severity}) `{finding.category}` `{finding.file}`: {finding.message}"
                )

        lines.extend(
            [
                "",
                "### Quick Fix",
                "- Open the `GitBot FullCheck` check run and click **Autofix** "
                "(updates this PR branch).",
                f"- Or comment `{self.settings.bot_command_prefix} autofix` to update this PR branch.",
            ]
        )
        return "\n".join(lines)

    def _safe_issue_comment(
        self, repo_name: str, issue_number: int, installation_id: int | None, body: str
    ) -> None:
        try:
            self.github.add_issue_comment(
                repo_name=repo_name,
                issue_number=issue_number,
                body=body,
                installation_id=installation_id,
            )
        except Exception:
            return

    def _react_to_comment(
        self, repo_name: str, comment_id: int, reaction: str, installation_id: int | None
    ) -> bool:
        try:
            self.github.add_issue_comment_reaction(
                repo_name=repo_name,
                comment_id=comment_id,
                reaction=reaction,
                installation_id=installation_id,
            )
            return True
        except Exception:
            return False

    def _post_meme(
        self,
        repo_name: str,
        pr_number: int,
        installation_id: int | None,
        bucket: str,
        reason: str,
        explicit_request: bool = False,
    ) -> None:
        if not self._should_post_meme(repo_name=repo_name, explicit_request=explicit_request):
            return

        generated = self.brain.generate_meme(
            bucket=bucket,
            reason=reason,
            repo_name=repo_name,
            pr_number=pr_number,
        )
        if generated:
            title = str(generated.get("title", "Meme Break")).strip() or "Meme Break"
            setup = str(generated.get("setup", "")).strip()
            punchline = str(generated.get("punchline", "")).strip()
        else:
            title = f"Meme Break `{bucket}`"
            setup = f"Reason: {reason}"
            punchline = "Vertex meme generator was unavailable for this event."

        body = (
            f"### {title}\n"
            f"_Bucket: `{bucket}`_\n\n"
            f"> {setup}\n\n"
            f"**{punchline}**"
        )
        self._safe_issue_comment(
            repo_name=repo_name,
            issue_number=pr_number,
            installation_id=installation_id,
            body=body,
        )

    def _post_final_meme(
        self,
        repo_name: str,
        pr_number: int,
        installation_id: int | None,
        reason: str,
        bucket: str | None = None,
        failed: bool = False,
        explicit_request: bool = False,
    ) -> None:
        if not self._should_post_meme(repo_name=repo_name, explicit_request=explicit_request):
            return
        resolved_bucket = bucket
        if not resolved_bucket:
            if failed:
                resolved_bucket = "fail"
            else:
                last_review = self.state.get_last_review(repo_name, pr_number)
                if last_review:
                    risk = int(last_review.get("risk_score", 50))
                    ok = risk < self.settings.high_risk_threshold
                    resolved_bucket = self._meme_bucket(risk_score=risk, ok=ok)
                else:
                    resolved_bucket = "good"
        self._post_meme(
            repo_name=repo_name,
            pr_number=pr_number,
            installation_id=installation_id,
            bucket=resolved_bucket,
            reason=reason,
            explicit_request=explicit_request,
        )

    def _should_post_meme(self, repo_name: str, explicit_request: bool) -> bool:
        if not self.state.get_meme_mode(repo_name):
            return False
        mode = self.settings.ai_meme_mode
        if mode == "always":
            return True
        if mode == "on-demand":
            return explicit_request
        return False

    @staticmethod
    def _meme_bucket(risk_score: int, ok: bool) -> str:
        if not ok and risk_score >= 70:
            return "fail"
        if risk_score >= 45:
            return "risky"
        return "good"

    @staticmethod
    def _pr_number_from_check_run_payload(payload: dict) -> int | None:
        check_run = payload.get("check_run", {})
        pull_requests = check_run.get("pull_requests") or []
        if pull_requests:
            number = pull_requests[0].get("number")
            if number is not None:
                return int(number)
        external_id = str(check_run.get("external_id", "")).strip()
        if "#" in external_id:
            try:
                return int(external_id.split("#", 1)[1])
            except ValueError:
                return None
        return None

    @staticmethod
    def _format_github_exception(exc: GithubException) -> str:
        message = ""
        data = getattr(exc, "data", None)
        if isinstance(data, dict):
            message = str(data.get("message", "")).strip()
        if not message:
            message = str(exc).strip() or "GitHub API error"

        if "Resource not accessible by integration" in message:
            return (
                "GitHub App lacks repository write access for this action. "
                "Set app permissions to `Contents: Read and write`, save changes, "
                "and re-install/refresh app permissions on the repository."
            )

        status = getattr(exc, "status", None)
        if status is None:
            return message
        return f"{message} (status={status})"
