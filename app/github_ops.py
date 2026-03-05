import re

from github.GithubException import GithubException, UnknownObjectException

from .ai import ChangedFile
from .auth import GitHubClientFactory
from .semver import bump_semver, parse_semver

SECRET_NAME_HINTS = (
    "token",
    "secret",
    "password",
    "api_key",
    "apikey",
    "private_key",
    "privatekey",
    "access_key",
    "client_secret",
)

PY_SECRET_ASSIGNMENT_RE = re.compile(
    r"""(?m)^(?P<indent>[ \t]*)(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"""
    r"""(?P<quote>["'])(?P<value>[^"']{4,})(?P=quote)\s*$"""
)

PY_SECRET_KWARG_RE = re.compile(
    r"""(?m)^(?P<indent>[ \t]*)(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"""
    r"""(?P<quote>["'])(?P<value>[^"']{4,})(?P=quote)(?P<trail>\s*,\s*)$"""
)

JS_SECRET_ASSIGNMENT_RE = re.compile(
    r"""(?m)^(?P<indent>[ \t]*)(?P<decl>(?:const|let|var)\s+)?"""
    r"""(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"""
    r"""(?P<quote>["'])(?P<value>[^"']{4,})(?P=quote)\s*;?\s*$"""
)


class GitHubOps:
    def __init__(self, client_factory: GitHubClientFactory) -> None:
        self.client_factory = client_factory

    def _repo(self, repo_name: str, installation_id: int | None):
        client = self.client_factory.get_client(installation_id=installation_id)
        return client.get_repo(repo_name)

    def get_pull_request(self, repo_name: str, number: int, installation_id: int | None):
        repo = self._repo(repo_name, installation_id)
        return repo, repo.get_pull(number)

    def list_pr_files(
        self,
        repo_name: str,
        number: int,
        max_files: int,
        max_patch_chars: int,
        installation_id: int | None,
    ) -> list[ChangedFile]:
        _, pr = self.get_pull_request(repo_name, number, installation_id)
        result: list[ChangedFile] = []
        for idx, file in enumerate(pr.get_files()):
            if max_files > 0 and idx >= max_files:
                break
            raw_patch = file.patch or ""
            patch = raw_patch if max_patch_chars <= 0 else raw_patch[:max_patch_chars]
            result.append(
                ChangedFile(
                    filename=file.filename,
                    status=file.status,
                    additions=file.additions,
                    deletions=file.deletions,
                    patch=patch,
                )
            )
        return result

    def post_pr_review(
        self,
        repo_name: str,
        number: int,
        body: str,
        event: str,
        installation_id: int | None,
    ) -> None:
        _, pr = self.get_pull_request(repo_name, number, installation_id)
        pr.create_review(body=body, event=event)

    def add_issue_comment(
        self, repo_name: str, issue_number: int, body: str, installation_id: int | None
    ) -> None:
        repo = self._repo(repo_name, installation_id)
        issue = repo.get_issue(number=issue_number)
        issue.create_comment(body)

    def add_issue_comment_reaction(
        self, repo_name: str, comment_id: int, reaction: str, installation_id: int | None
    ) -> None:
        repo = self._repo(repo_name, installation_id)
        comment = repo.get_issue_comment(comment_id)
        comment.create_reaction(reaction)

    def bump_version_file(
        self, repo_name: str, branch: str, bump_type: str, installation_id: int | None
    ) -> tuple[str, str]:
        repo = self._repo(repo_name, installation_id)
        try:
            current = repo.get_contents("VERSION", ref=branch)
            old_version = current.decoded_content.decode("utf-8").strip()
            parse_semver(old_version)
            new_version = bump_semver(old_version, bump_type)
            repo.update_file(
                path="VERSION",
                message=f"chore(release): bump version {old_version} -> {new_version}",
                content=f"{new_version}\n",
                sha=current.sha,
                branch=branch,
            )
            return old_version, new_version
        except UnknownObjectException:
            base_version = "0.1.0"
            new_version = bump_semver(base_version, bump_type)
            repo.create_file(
                path="VERSION",
                message=f"chore(release): initialize version {new_version}",
                content=f"{new_version}\n",
                branch=branch,
            )
            return base_version, new_version

    def get_current_version(self, repo_name: str, branch: str, installation_id: int | None) -> str:
        repo = self._repo(repo_name, installation_id)
        current = repo.get_contents("VERSION", ref=branch)
        version = current.decoded_content.decode("utf-8").strip()
        parse_semver(version)
        return version

    def ensure_tag_and_release(
        self,
        repo_name: str,
        version: str,
        target_branch: str,
        notes: str,
        installation_id: int | None,
    ) -> str:
        repo = self._repo(repo_name, installation_id)
        tag_name = f"v{version}"

        try:
            repo.get_git_ref(f"tags/{tag_name}")
        except UnknownObjectException:
            head_ref = repo.get_git_ref(f"heads/{target_branch}")
            repo.create_git_ref(ref=f"refs/tags/{tag_name}", sha=head_ref.object.sha)

        try:
            repo.get_release(tag_name)
        except UnknownObjectException:
            repo.create_git_release(
                tag=tag_name,
                name=f"Release {tag_name}",
                message=notes,
                target_commitish=target_branch,
                draft=False,
                prerelease=False,
            )
        return tag_name

    def checks_green(
        self, repo_name: str, number: int, installation_id: int | None
    ) -> tuple[bool, list[str]]:
        repo, pr = self.get_pull_request(repo_name, number, installation_id)
        commit = repo.get_commit(pr.head.sha)
        combined = commit.get_combined_status()
        statuses = list(combined.statuses)
        if not statuses:
            return True, ["No status checks found for head commit."]

        details: list[str] = []
        for status in statuses[:20]:
            details.append(f"{status.context}: {status.state}")
        is_green = all(status.state == "success" for status in statuses)
        return is_green, details

    def merge_pull_request(
        self,
        repo_name: str,
        number: int,
        merge_method: str,
        installation_id: int | None,
    ) -> dict[str, str | bool]:
        _, pr = self.get_pull_request(repo_name, number, installation_id)
        if pr.merged:
            return {
                "merged": True,
                "message": "Pull request is already merged.",
                "sha": str(pr.merge_commit_sha or ""),
                "method": merge_method,
            }

        result = pr.merge(merge_method=merge_method)
        merged = bool(getattr(result, "merged", False))
        message = str(getattr(result, "message", "") or "")
        sha = str(getattr(result, "sha", "") or "")
        return {
            "merged": merged,
            "message": message,
            "sha": sha,
            "method": merge_method,
        }

    def set_pull_request_state(
        self,
        repo_name: str,
        number: int,
        state: str,
        installation_id: int | None,
    ) -> dict[str, str | bool]:
        target = state.lower().strip()
        if target not in {"open", "closed"}:
            raise ValueError("state must be 'open' or 'closed'")

        _, pr = self.get_pull_request(repo_name, number, installation_id)
        current_state = str(getattr(pr, "state", "")).lower().strip()
        if target == current_state:
            return {
                "changed": False,
                "state": current_state,
                "message": f"Pull request is already {current_state}.",
            }
        if target == "open" and bool(getattr(pr, "merged", False)):
            return {
                "changed": False,
                "state": "closed",
                "message": "Merged pull request cannot be reopened.",
            }

        pr.edit(state=target)
        return {
            "changed": True,
            "state": target,
            "message": f"Pull request state set to {target}.",
        }

    def add_labels(
        self, repo_name: str, issue_number: int, labels: list[str], installation_id: int | None
    ) -> list[str]:
        if not labels:
            return []

        repo = self._repo(repo_name, installation_id)
        existing = {label.name for label in repo.get_labels()}
        created: list[str] = []
        for label in labels:
            if label in existing:
                continue
            try:
                repo.create_label(name=label, color="BFDADC", description=f"Managed by {repo_name} bot")
                created.append(label)
            except GithubException:
                continue

        issue = repo.get_issue(number=issue_number)
        issue.add_to_labels(*labels)
        return created

    def recent_merged_prs(
        self, repo_name: str, base_branch: str, limit: int, installation_id: int | None
    ) -> list[dict]:
        repo = self._repo(repo_name, installation_id)
        merged: list[dict] = []
        for pr in repo.get_pulls(
            state="closed", sort="updated", direction="desc", base=base_branch
        ):
            if not pr.merged:
                continue
            merged.append(
                {
                    "number": pr.number,
                    "title": pr.title,
                    "html_url": pr.html_url,
                    "labels": [label.name for label in pr.labels],
                }
            )
            if len(merged) >= limit:
                break
        return merged

    @staticmethod
    def _line_ending(line: str, default: str) -> str:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith("\n"):
            return "\n"
        return default

    @staticmethod
    def _is_autofixable_file(path: str) -> bool:
        return path.lower().endswith((".py", ".js", ".ts", ".tsx", ".jsx"))

    @staticmethod
    def _is_secret_variable_name(name: str) -> bool:
        lower = name.lower()
        return any(hint in lower for hint in SECRET_NAME_HINTS)

    @staticmethod
    def _env_name_for_var(name: str) -> str:
        interim = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
        normalized = re.sub(r"[^A-Za-z0-9]+", "_", interim).strip("_").upper()
        return normalized or "SECRET_VALUE"

    @staticmethod
    def _intent_from_message(message: str) -> set[str]:
        normalized = message.lower()
        intents: set[str] = set()
        if "debug logging found (print" in normalized:
            intents.add("remove_print")
        if "debug logging found (console.log" in normalized:
            intents.add("remove_console_log")
        if "todo marker found" in normalized:
            intents.add("todo_cleanup")
        if "fixme marker found" in normalized:
            intents.add("fixme_cleanup")
        if "broad exception catch detected" in normalized:
            intents.add("narrow_exception")
        if (
            "hardcoded secret" in normalized
            or "secret handling" in normalized
            or "private key handling" in normalized
            or "token hardcoding" in normalized
            or "credential handling" in normalized
        ):
            intents.add("secret_to_env")
        if "use of eval detected" in normalized:
            intents.add("eval_found")
        if "use of exec detected" in normalized:
            intents.add("exec_found")
        return intents

    @classmethod
    def _ensure_python_os_import(cls, content: str) -> str:
        if re.search(r"(?m)^\s*import\s+os(?:\s|$)", content):
            return content
        if re.search(r"(?m)^\s*from\s+os\s+import\s+", content):
            return content

        newline = "\r\n" if "\r\n" in content else "\n"
        lines = content.splitlines(keepends=True)
        insert_index = 0

        if lines and lines[0].startswith("#!"):
            insert_index = 1
        if (
            insert_index < len(lines)
            and "coding" in lines[insert_index]
            and lines[insert_index].lstrip().startswith("#")
        ):
            insert_index += 1
        while (
            insert_index < len(lines)
            and lines[insert_index].lstrip().startswith("from __future__ import")
        ):
            insert_index += 1

        lines.insert(insert_index, f"import os{newline}")
        return "".join(lines)

    @classmethod
    def _apply_python_autofix(cls, content: str, intents: set[str]) -> tuple[str, list[str]]:
        applied: list[str] = []
        newline = "\r\n" if "\r\n" in content else "\n"
        lines = content.splitlines(keepends=True)

        if "remove_print" in intents:
            changed = False
            for idx, line in enumerate(lines):
                raw = line.rstrip("\r\n")
                match = re.match(r"^([ \t]*)print\(.+\)\s*(?:#.*)?$", raw)
                if not match:
                    continue
                indent = match.group(1)
                lines[idx] = f"{indent}pass  # gitbot: removed debug print{cls._line_ending(line, newline)}"
                changed = True
            if changed:
                applied.append("remove_print")

        if "narrow_exception" in intents:
            changed_alias = False
            changed_reraise = False
            for idx, line in enumerate(lines):
                raw = line.rstrip("\r\n")
                match = re.match(r"^([ \t]*)except\s+Exception\s*:\s*$", raw)
                if not match:
                    continue
                indent = match.group(1)
                lines[idx] = f"{indent}except Exception as exc:{cls._line_ending(line, newline)}"
                changed_alias = True
                if idx + 1 >= len(lines):
                    continue
                next_raw = lines[idx + 1].rstrip("\r\n")
                pass_match = re.match(r"^([ \t]*)pass(?:\s+#.*)?\s*$", next_raw)
                if not pass_match:
                    continue
                body_indent = pass_match.group(1)
                if len(body_indent) <= len(indent):
                    continue
                lines[idx + 1] = f"{body_indent}raise{cls._line_ending(lines[idx + 1], newline)}"
                changed_reraise = True
            if changed_alias:
                applied.append("narrow_exception")
            if changed_reraise:
                applied.append("replace_pass_with_raise")

        updated = "".join(lines)

        if "todo_cleanup" in intents:
            updated, count = re.subn(r"\bTODO\b", "TRACKED_TASK", updated, flags=re.IGNORECASE)
            if count:
                applied.append("todo_cleanup")
        if "fixme_cleanup" in intents:
            updated, count = re.subn(r"\bFIXME\b", "TRACKED_TASK", updated, flags=re.IGNORECASE)
            if count:
                applied.append("fixme_cleanup")

        if "secret_to_env" in intents:
            secret_replaced = False

            def _replace_secret(match: re.Match[str]) -> str:
                nonlocal secret_replaced
                name = match.group("name")
                if not cls._is_secret_variable_name(name):
                    return match.group(0)
                env_name = cls._env_name_for_var(name)
                secret_replaced = True
                trail = match.groupdict().get("trail", "")
                if trail:
                    return f'{match.group("indent")}{name}=os.getenv("{env_name}", ""){trail}'
                return f'{match.group("indent")}{name} = os.getenv("{env_name}", "")'

            updated = PY_SECRET_ASSIGNMENT_RE.sub(_replace_secret, updated)
            updated = PY_SECRET_KWARG_RE.sub(_replace_secret, updated)
            if secret_replaced:
                updated = cls._ensure_python_os_import(updated)
                applied.append("secret_to_env")

        return updated, applied

    @classmethod
    def _apply_js_autofix(cls, content: str, intents: set[str]) -> tuple[str, list[str]]:
        applied: list[str] = []
        newline = "\r\n" if "\r\n" in content else "\n"
        lines = content.splitlines(keepends=True)

        if "remove_console_log" in intents:
            changed = False
            for idx, line in enumerate(lines):
                raw = line.rstrip("\r\n")
                match = re.match(r"^([ \t]*)console\.log\(.+\)\s*;?\s*(?:\/\/.*)?$", raw)
                if not match:
                    continue
                indent = match.group(1)
                lines[idx] = f"{indent}// gitbot: removed debug log{cls._line_ending(line, newline)}"
                changed = True
            if changed:
                applied.append("remove_console_log")

        updated = "".join(lines)

        if "todo_cleanup" in intents:
            updated, count = re.subn(r"\bTODO\b", "TRACKED_TASK", updated, flags=re.IGNORECASE)
            if count:
                applied.append("todo_cleanup")
        if "fixme_cleanup" in intents:
            updated, count = re.subn(r"\bFIXME\b", "TRACKED_TASK", updated, flags=re.IGNORECASE)
            if count:
                applied.append("fixme_cleanup")

        if "secret_to_env" in intents:
            secret_replaced = False

            def _replace_secret(match: re.Match[str]) -> str:
                nonlocal secret_replaced
                name = match.group("name")
                if not cls._is_secret_variable_name(name):
                    return match.group(0)
                decl = match.group("decl") or ""
                env_name = cls._env_name_for_var(name)
                secret_replaced = True
                return f'{match.group("indent")}{decl}{name} = process.env.{env_name} || "";'

            updated = JS_SECRET_ASSIGNMENT_RE.sub(_replace_secret, updated)
            if secret_replaced:
                applied.append("secret_to_env")

        return updated, applied

    @classmethod
    def _apply_safe_autofixes(
        cls, path: str, content: str, findings: list[dict]
    ) -> tuple[str, list[str], list[str]]:
        intents: set[str] = set()
        for finding in findings:
            intents.update(cls._intent_from_message(str(finding.get("message", ""))))

        unresolved: list[str] = []
        if "eval_found" in intents:
            unresolved.append("eval usage requires manual refactor.")
        if "exec_found" in intents:
            unresolved.append("exec usage requires manual refactor.")

        lower = path.lower()
        if lower.endswith(".py"):
            updated, applied = cls._apply_python_autofix(content, intents)
            if "secret_to_env" in intents and "secret_to_env" not in applied:
                unresolved.append("secret finding did not match safe rewrite pattern.")
            return updated, sorted(set(applied)), unresolved
        if lower.endswith((".js", ".ts", ".tsx", ".jsx")):
            updated, applied = cls._apply_js_autofix(content, intents)
            if "secret_to_env" in intents and "secret_to_env" not in applied:
                unresolved.append("secret finding did not match safe rewrite pattern.")
            return updated, sorted(set(applied)), unresolved

        unresolved.append("file type not supported for autofix.")
        return content, [], unresolved

    def apply_autofix_to_pr_branch(
        self,
        repo_name: str,
        source_pr_number: int,
        findings: list[dict],
        installation_id: int | None,
    ) -> dict[str, object]:
        repo, pr = self.get_pull_request(repo_name, source_pr_number, installation_id)
        target_branch = pr.head.ref

        findings_by_file: dict[str, list[dict]] = {}
        autofixable_categories = {"security", "quality"}
        for finding in findings:
            category = str(finding.get("category", "")).strip().lower()
            if category and category not in autofixable_categories:
                continue
            file_path = str(finding.get("file", "")).strip()
            if not file_path or file_path == "*":
                continue
            findings_by_file.setdefault(file_path, []).append(finding)

        changed_files: list[str] = []
        applied_rules: list[str] = []
        unresolved: list[str] = []
        commit_urls: list[str] = []

        for path, file_findings in findings_by_file.items():
            if not self._is_autofixable_file(path):
                unresolved.append(f"{path}: file type not supported for autofix.")
                continue

            try:
                current = repo.get_contents(path, ref=target_branch)
            except UnknownObjectException:
                unresolved.append(f"{path}: file not found on branch.")
                continue

            try:
                original = current.decoded_content.decode("utf-8")
            except UnicodeDecodeError:
                unresolved.append(f"{path}: file is not UTF-8 text.")
                continue

            updated, applied, unresolved_file = self._apply_safe_autofixes(path, original, file_findings)
            unresolved.extend(f"{path}: {item}" for item in unresolved_file)
            if updated == original:
                continue

            result = repo.update_file(
                path=path,
                message=f"chore(gitbot): autofix findings in {path}",
                content=updated,
                sha=current.sha,
                branch=target_branch,
            )
            changed_files.append(path)
            applied_rules.extend(f"{path}: {rule}" for rule in applied)

            commit_obj = result.get("commit")
            if commit_obj is not None:
                commit_url = str(getattr(commit_obj, "html_url", "") or "")
                if commit_url:
                    commit_urls.append(commit_url)

        if not findings_by_file:
            unresolved.append("No security/quality file findings available to autofix.")

        return {
            "branch": target_branch,
            "commit_url": commit_urls[-1] if commit_urls else "",
            "commit_urls": commit_urls,
            "changed_count": len(changed_files),
            "changed_files": changed_files,
            "applied_rules": sorted(set(applied_rules)),
            "unresolved": sorted(set(unresolved)),
        }

    def create_check_run_with_actions(
        self,
        repo_name: str,
        *,
        head_sha: str,
        name: str,
        summary: str,
        details_text: str,
        external_id: str,
        actions: list[dict[str, str]],
        installation_id: int | None,
        conclusion: str = "neutral",
    ) -> str:
        repo = self._repo(repo_name, installation_id)
        safe_actions: list[dict[str, str]] = []
        for action in actions:
            safe_actions.append(
                {
                    "label": str(action.get("label", ""))[:20],
                    "description": str(action.get("description", ""))[:40],
                    "identifier": str(action.get("identifier", ""))[:20],
                }
            )
        check_run = repo.create_check_run(
            name=name,
            head_sha=head_sha,
            external_id=external_id,
            status="completed",
            conclusion=conclusion,
            output={
                "title": name,
                "summary": summary[:65000],
                "text": details_text[:65000],
            },
            actions=safe_actions,
        )
        return check_run.html_url
