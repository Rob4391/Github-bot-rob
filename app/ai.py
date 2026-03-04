import json
import re
from dataclasses import asdict, dataclass


ALLOWED_CHECKS = {"security", "tests", "docs", "dependencies", "size", "quality"}
HARDCODED_SECRET_RE = re.compile(
    r"""\b
    (api[_-]?key|secret|token|password|private[_-]?key)
    \b
    \s*[:=]\s*
    (["']).{4,}\2
    """,
    re.IGNORECASE | re.VERBOSE,
)
EXCEPT_EXCEPTION_RE = re.compile(r"^\s*except\s+Exception\s*:\s*$")
EVAL_CALL_RE = re.compile(r"""(?<!["'])\beval\s*\(""", re.IGNORECASE)
EXEC_CALL_RE = re.compile(r"""(?<!["'])\bexec\s*\(""", re.IGNORECASE)


@dataclass(frozen=True)
class ChangedFile:
    filename: str
    status: str
    additions: int
    deletions: int
    patch: str


@dataclass(frozen=True)
class Finding:
    id: str
    severity: str
    category: str
    file: str
    message: str
    suggestion: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "Finding":
        return cls(
            id=str(payload.get("id", "")),
            severity=str(payload.get("severity", "low")),
            category=str(payload.get("category", "quality")),
            file=str(payload.get("file", "")),
            message=str(payload.get("message", "")),
            suggestion=str(payload.get("suggestion", "")),
        )


@dataclass(frozen=True)
class ReviewResult:
    summary: str
    findings: list[Finding]
    approve: bool
    confidence: float
    source: str
    risk_score: int
    check_results: dict[str, bool]
    suggestions: list[str]
    profile: str


class BotIntelligence:
    def __init__(self, openai_api_key: str | None, model: str) -> None:
        self.openai_api_key = openai_api_key
        self.model = model

    def review_pull_request(
        self,
        title: str,
        description: str,
        changed_files: list[ChangedFile],
        profile: str = "balanced",
        enabled_checks: set[str] | None = None,
        memory_notes: list[str] | None = None,
    ) -> ReviewResult:
        checks = self._normalize_checks(enabled_checks)
        notes = memory_notes or []
        if self.openai_api_key:
            llm_result = self._review_with_openai(
                title=title,
                description=description,
                changed_files=changed_files,
                profile=profile,
                checks=checks,
            )
            if llm_result is not None:
                return llm_result
        return self._review_with_rules(
            title=title,
            changed_files=changed_files,
            profile=profile,
            checks=checks,
            memory_notes=notes,
        )

    @staticmethod
    def _normalize_checks(enabled_checks: set[str] | None) -> set[str]:
        if not enabled_checks:
            return set(ALLOWED_CHECKS)
        return {item for item in enabled_checks if item in ALLOWED_CHECKS}

    @staticmethod
    def _new_finding(
        severity: str,
        category: str,
        file: str,
        message: str,
        suggestion: str,
    ) -> Finding:
        return Finding(
            id="",
            severity=severity,
            category=category,
            file=file,
            message=message,
            suggestion=suggestion,
        )

    @staticmethod
    def _assign_finding_ids(findings: list[Finding]) -> list[Finding]:
        result: list[Finding] = []
        for idx, finding in enumerate(findings, start=1):
            result.append(
                Finding(
                    id=f"F{idx}",
                    severity=finding.severity,
                    category=finding.category,
                    file=finding.file,
                    message=finding.message,
                    suggestion=finding.suggestion,
                )
            )
        return result

    @staticmethod
    def _profile_threshold(profile: str) -> int:
        if profile == "strict":
            return 20
        if profile == "fast":
            return 55
        return 35

    @staticmethod
    def _build_check_results(checks: set[str], findings: list[Finding]) -> dict[str, bool]:
        by_category = {name: [] for name in checks}
        for finding in findings:
            if finding.category in by_category:
                by_category[finding.category].append(finding)
        results: dict[str, bool] = {}
        for name in checks:
            has_high = any(item.severity == "high" for item in by_category[name])
            has_medium = any(item.severity == "medium" for item in by_category[name])
            if name in {"tests", "docs"}:
                results[name] = not (has_high or has_medium)
            else:
                results[name] = not has_high
        return results

    def _review_with_openai(
        self,
        title: str,
        description: str,
        changed_files: list[ChangedFile],
        profile: str,
        checks: set[str],
    ) -> ReviewResult | None:
        try:
            from openai import OpenAI
        except Exception as exc:
            return None

        client = OpenAI(api_key=self.openai_api_key)
        payload = {
            "title": title,
            "description": description[:4000],
            "profile": profile,
            "checks": sorted(checks),
            "files": [
                {
                    "filename": f.filename,
                    "status": f.status,
                    "additions": f.additions,
                    "deletions": f.deletions,
                    "patch": f.patch[:3000],
                }
                for f in changed_files
            ],
            "output_format": {
                "summary": "string",
                "findings": [
                    {
                        "severity": "low|medium|high",
                        "category": "security|tests|docs|dependencies|size|quality",
                        "file": "string",
                        "message": "string",
                        "suggestion": "string",
                    }
                ],
                "risk_score": "integer 0-100",
                "approve": "boolean",
                "confidence": "number between 0 and 1",
            },
        }
        prompt = (
            "You are a strict GitHub code review bot. Return only JSON matching output_format. "
            "Focus on concrete risks in changed code and avoid generic advice."
        )

        try:
            response = client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(payload)},
                ],
            )
            data = json.loads(response.output_text)
            findings: list[Finding] = []
            for raw in data.get("findings", []):
                severity = str(raw.get("severity", "low")).lower().strip()
                if severity not in {"low", "medium", "high"}:
                    severity = "low"
                category = str(raw.get("category", "quality")).lower().strip()
                if category not in ALLOWED_CHECKS:
                    category = "quality"
                findings.append(
                    self._new_finding(
                        severity=severity,
                        category=category,
                        file=str(raw.get("file", "")),
                        message=str(raw.get("message", "")).strip(),
                        suggestion=str(raw.get("suggestion", "")).strip(),
                    )
                )

            findings = self._dedupe_findings(findings)
            findings = self._assign_finding_ids(findings)
            risk_score = int(data.get("risk_score", 50))
            risk_score = max(0, min(100, risk_score))
            check_results = self._build_check_results(checks, findings)
            confidence = float(data.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))
            suggestions = [item.suggestion for item in findings if item.suggestion]
            return ReviewResult(
                summary=str(data.get("summary", "LLM review generated.")).strip(),
                findings=findings,
                approve=bool(data.get("approve", False)),
                confidence=confidence,
                source="openai",
                risk_score=risk_score,
                check_results=check_results,
                suggestions=sorted(set(suggestions)),
                profile=profile,
            )
        except Exception as exc:
            return None

    def _review_with_rules(
        self,
        title: str,
        changed_files: list[ChangedFile],
        profile: str,
        checks: set[str],
        memory_notes: list[str],
    ) -> ReviewResult:
        effective_files = [
            item for item in changed_files if not item.filename.startswith(".gitbot/")
        ]
        findings: list[Finding] = []
        total_delta = sum(f.additions + f.deletions for f in effective_files)
        src_changed = any(self._is_source_file(f.filename) for f in effective_files)
        tests_changed = any(self._is_test_file(f.filename) for f in effective_files)
        docs_changed = any(self._is_docs_file(f.filename) for f in effective_files)
        dependency_files = [f for f in effective_files if self._is_dependency_file(f.filename)]

        if "size" in checks:
            if len(effective_files) > 20:
                findings.append(
                    self._new_finding(
                        severity="medium",
                        category="size",
                        file="*",
                        message="Large PR: more than 20 files changed.",
                        suggestion="Split this PR into smaller focused changes where possible.",
                    )
                )
            if total_delta > 1200:
                findings.append(
                    self._new_finding(
                        severity="high",
                        category="size",
                        file="*",
                        message="Large PR: more than 1200 total line changes.",
                        suggestion="Reduce change size or add an explicit rollout/testing plan in PR.",
                    )
                )
            elif total_delta > 400:
                findings.append(
                    self._new_finding(
                        severity="low",
                        category="size",
                        file="*",
                        message="Moderate PR size may slow review quality.",
                        suggestion="Consider breaking into smaller commits for easier verification.",
                    )
                )

        if "tests" in checks and src_changed and not tests_changed:
            findings.append(
                self._new_finding(
                    severity="medium",
                    category="tests",
                    file="*",
                    message="Source code changed without matching test changes.",
                    suggestion="Add or update tests for the modified behavior.",
                )
            )

        if "docs" in checks and src_changed and not docs_changed:
            findings.append(
                self._new_finding(
                    severity="low",
                    category="docs",
                    file="*",
                    message="Source changes detected without docs/changelog updates.",
                    suggestion="Update README/docs/changelog if behavior or interfaces changed.",
                )
            )

        if "dependencies" in checks and dependency_files:
            for dep_file in dependency_files:
                findings.append(
                    self._new_finding(
                        severity="low",
                        category="dependencies",
                        file=dep_file.filename,
                        message="Dependency manifest changed; run security and license scan.",
                        suggestion="Run dependency audit and include results in the PR.",
                    )
                )

        if "security" in checks or "quality" in checks:
            for changed in effective_files:
                if not self._is_source_file(changed.filename):
                    continue
                for line in self._added_code_lines(changed.patch):
                    normalized_line = line.lower()
                    if "security" in checks:
                        if EVAL_CALL_RE.search(line):
                            findings.append(
                                self._new_finding(
                                    severity="high",
                                    category="security",
                                    file=changed.filename,
                                    message="Use of eval detected.",
                                    suggestion=self._suggestion_for_token("eval("),
                                )
                            )
                        if EXEC_CALL_RE.search(line):
                            findings.append(
                                self._new_finding(
                                    severity="high",
                                    category="security",
                                    file=changed.filename,
                                    message="Use of exec detected.",
                                    suggestion=self._suggestion_for_token("exec("),
                                )
                            )
                        if HARDCODED_SECRET_RE.search(line):
                            findings.append(
                                self._new_finding(
                                    severity="high",
                                    category="security",
                                    file=changed.filename,
                                    message="Potential hardcoded secret detected.",
                                    suggestion=self._suggestion_for_token("api_key"),
                                )
                            )

                    if "quality" in checks:
                        if EXCEPT_EXCEPTION_RE.search(line):
                            findings.append(
                                self._new_finding(
                                    severity="medium",
                                    category="quality",
                                    file=changed.filename,
                                    message="Broad exception catch detected.",
                                    suggestion=self._suggestion_for_token("except Exception"),
                                )
                            )
                        if "todo" in normalized_line:
                            findings.append(
                                self._new_finding(
                                    severity="low",
                                    category="quality",
                                    file=changed.filename,
                                    message="TODO marker found in changed code.",
                                    suggestion=self._suggestion_for_token("TODO"),
                                )
                            )
                        if "fixme" in normalized_line:
                            findings.append(
                                self._new_finding(
                                    severity="low",
                                    category="quality",
                                    file=changed.filename,
                                    message="FIXME marker found in changed code.",
                                    suggestion=self._suggestion_for_token("FIXME"),
                                )
                            )
                        stripped = line.strip()
                        if stripped.startswith("console.log("):
                            findings.append(
                                self._new_finding(
                                    severity="low",
                                    category="quality",
                                    file=changed.filename,
                                    message="Debug logging found (console.log).",
                                    suggestion=self._suggestion_for_token("console.log("),
                                )
                            )
                        if stripped.startswith("print("):
                            findings.append(
                                self._new_finding(
                                    severity="low",
                                    category="quality",
                                    file=changed.filename,
                                    message="Debug logging found (print).",
                                    suggestion=self._suggestion_for_token("print("),
                                )
                            )

        findings = self._dedupe_findings(findings)
        findings = self._assign_finding_ids(findings)
        risk_score = self._risk_score(total_delta=total_delta, findings=findings)
        check_results = self._build_check_results(checks, findings)
        threshold = self._profile_threshold(profile)
        has_high = any(item.severity == "high" for item in findings)
        approve = (not has_high) and risk_score <= threshold
        if profile == "strict":
            approve = approve and check_results.get("tests", True)
        confidence = max(0.2, min(0.95, 1.0 - (risk_score / 130.0)))

        note_suffix = ""
        if memory_notes:
            note_suffix = f" Memory notes: {len(memory_notes)}."

        summary = (
            f"Rule-based review for PR '{title}': "
            f"{'no blocking issues found' if approve else 'potential risks found'}."
            f" Risk score: {risk_score}/100. Profile: {profile}.{note_suffix}"
        )
        suggestions = [item.suggestion for item in findings if item.suggestion]
        return ReviewResult(
            summary=summary,
            findings=findings,
            approve=approve,
            confidence=confidence,
            source="rules",
            risk_score=risk_score,
            check_results=check_results,
            suggestions=sorted(set(suggestions)),
            profile=profile,
        )

    @staticmethod
    def _risk_score(total_delta: int, findings: list[Finding]) -> int:
        weights = {"low": 6, "medium": 12, "high": 22}
        score = min(25, total_delta // 80)
        score += sum(weights.get(item.severity, 4) for item in findings)
        return max(0, min(100, score))

    @staticmethod
    def _dedupe_findings(findings: list[Finding]) -> list[Finding]:
        seen: set[tuple[str, str, str, str]] = set()
        result: list[Finding] = []
        for item in findings:
            key = (item.severity, item.category, item.file, item.message)
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        result.sort(key=lambda it: (it.severity != "high", it.severity != "medium", it.file, it.message))
        return result

    @staticmethod
    def _is_source_file(path: str) -> bool:
        lower = path.lower()
        return lower.endswith((".py", ".js", ".ts", ".tsx", ".go", ".java", ".rb"))

    @staticmethod
    def _is_test_file(path: str) -> bool:
        lower = path.lower()
        return (
            lower.startswith("tests/")
            or "/test" in lower
            or lower.endswith(("_test.py", ".spec.ts", ".spec.js"))
        )

    @staticmethod
    def _is_docs_file(path: str) -> bool:
        lower = path.lower()
        return lower.startswith("docs/") or lower.endswith((".md", ".rst"))

    @staticmethod
    def _is_dependency_file(path: str) -> bool:
        name = path.lower().split("/")[-1]
        return name in {
            "requirements.txt",
            "poetry.lock",
            "pyproject.toml",
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "go.mod",
            "go.sum",
        }

    @staticmethod
    def _added_code_lines(patch: str) -> list[str]:
        lines: list[str] = []
        for raw in (patch or "").splitlines():
            if not raw.startswith("+") or raw.startswith("+++"):
                continue
            line = raw[1:].strip()
            if not line:
                continue
            if line.startswith(("#", "//", "/*", "*", "*/")):
                continue
            lines.append(line)
        return lines

    @staticmethod
    def _suggestion_for_token(token: str) -> str:
        normalized = token.lower()
        if normalized in {"eval(", "exec("}:
            return "Avoid dynamic code execution; replace with explicit function dispatch."
        if normalized in {"password", "api_key", "private_key", "token="}:
            return "Use secret manager/environment variables and avoid plaintext secrets in code."
        if normalized == "except exception":
            return "Catch specific exceptions and re-raise unexpected errors."
        if normalized in {"todo", "fixme"}:
            return "Convert TODO/FIXME into a tracked issue and remove from production path."
        if normalized in {"console.log(", "print("}:
            return "Replace debug print/log with structured logger at the right log level."
        return "Review this change and apply a safer implementation."
