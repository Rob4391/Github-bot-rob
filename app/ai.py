import json
import logging
import re
import time
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
logger = logging.getLogger("gitbot.ai")


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
    def __init__(
        self,
        openai_api_key: str | None,
        model: str,
        ai_provider: str = "auto",
        vertex_project: str | None = None,
        vertex_location: str = "us-central1",
        vertex_model: str = "gemini-2.0-flash-001",
        vertex_timeout_seconds: int = 300,
        vertex_max_output_tokens: int = 512,
        vertex_temperature: float = 0.0,
        llm_required: bool = False,
    ) -> None:
        self.openai_api_key = openai_api_key
        self.model = model
        self.ai_provider = ai_provider
        project = (vertex_project or "").strip()
        self.vertex_project = project or None
        self.vertex_location = (vertex_location or "us-central1").strip() or "us-central1"
        self.vertex_model = (vertex_model or "gemini-2.0-flash-001").strip()
        self.vertex_timeout_seconds = max(1, int(vertex_timeout_seconds))
        self.vertex_max_output_tokens = max(64, int(vertex_max_output_tokens))
        self.vertex_temperature = max(0.0, min(1.0, float(vertex_temperature)))
        self.vertex_files_per_batch = 20
        self.llm_required = llm_required

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
        llm_result: ReviewResult | None = None

        if self.ai_provider == "openai":
            llm_result = self._review_with_openai(
                title=title,
                description=description,
                changed_files=changed_files,
                profile=profile,
                checks=checks,
            )
        elif self.ai_provider == "vertex":
            llm_result = self._review_with_vertex(
                title=title,
                description=description,
                changed_files=changed_files,
                profile=profile,
                checks=checks,
            )
        elif self.ai_provider == "auto":
            if self.openai_api_key:
                llm_result = self._review_with_openai(
                    title=title,
                    description=description,
                    changed_files=changed_files,
                    profile=profile,
                    checks=checks,
                )
            if llm_result is None:
                llm_result = self._review_with_vertex(
                    title=title,
                    description=description,
                    changed_files=changed_files,
                    profile=profile,
                    checks=checks,
                )
        elif self.ai_provider == "rules":
            llm_result = None

        if llm_result is not None:
            return llm_result

        if self.llm_required and self.ai_provider != "rules":
            return self._llm_unavailable_result(profile=profile, checks=checks)

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
        if not self.openai_api_key:
            return None

        try:
            from openai import OpenAI
        except Exception:
            return None

        client = OpenAI(api_key=self.openai_api_key)
        try:
            payload = self._llm_request_payload(
                title=title,
                description=description,
                changed_files=changed_files,
                profile=profile,
                checks=checks,
            )
            response = client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": self._llm_system_prompt()},
                    {"role": "user", "content": json.dumps(payload)},
                ],
            )
            text = str(getattr(response, "output_text", "") or "").strip()
            data = self._json_from_text(text)
            if data is None:
                return None
            return self._review_result_from_llm_data(
                data=data,
                profile=profile,
                checks=checks,
                source="openai",
            )
        except Exception:
            return None

    def _review_with_vertex(
        self,
        title: str,
        description: str,
        changed_files: list[ChangedFile],
        profile: str,
        checks: set[str],
    ) -> ReviewResult | None:
        if not self.vertex_model:
            return None
        if not self.vertex_project:
            if self.ai_provider == "vertex":
                logger.warning("Vertex AI project missing. Set GOOGLE_CLOUD_PROJECT or GCP_PROJECT.")
            return None

        try:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession
        except Exception as exc:
            logger.warning("Vertex AI dependencies unavailable: %s", exc)
            return None

        try:
            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        except Exception as exc:
            logger.warning("Vertex AI auth bootstrap failed: %s", exc)
            return None

        project = self.vertex_project
        files = changed_files or []
        batch_count = max(1, (len(files) + self.vertex_files_per_batch - 1) // self.vertex_files_per_batch)
        batched_files = [
            files[idx : idx + self.vertex_files_per_batch]
            for idx in range(0, len(files), self.vertex_files_per_batch)
        ] or [[]]
        endpoint = self._vertex_endpoint(project)

        try:
            started = time.perf_counter()
            session = AuthorizedSession(credentials)
            batch_payloads: list[dict] = []
            for index, batch in enumerate(batched_files, start=1):
                payload = self._llm_request_payload(
                    title=title,
                    description=description,
                    changed_files=batch,
                    profile=profile,
                    checks=checks,
                    total_files_changed=len(files),
                    batch_index=index,
                    batch_count=batch_count,
                )
                data = self._request_vertex_json(
                    session=session,
                    endpoint=endpoint,
                    payload=payload,
                )
                if data is None:
                    logger.warning(
                        "Vertex review returned unparsable JSON project=%s location=%s model=%s batch=%s/%s files_in_batch=%s",
                        project,
                        self.vertex_location,
                        self.vertex_model,
                        index,
                        batch_count,
                        len(batch),
                    )
                    return None
                batch_payloads.append(data)
            merged = self._merge_vertex_batch_payloads(batch_payloads, len(files), batch_count)
            duration = time.perf_counter() - started
            logger.info(
                "Vertex review completed project=%s location=%s model=%s duration=%.2fs files=%s batches=%s",
                project,
                self.vertex_location,
                self.vertex_model,
                duration,
                len(files),
                batch_count,
            )
            return self._review_result_from_llm_data(
                data=merged,
                profile=profile,
                checks=checks,
                source="vertex",
            )
        except Exception as exc:
            logger.warning(
                "Vertex review failed project=%s location=%s model=%s error=%s",
                project,
                self.vertex_location,
                self.vertex_model,
                exc,
            )
            return None

    def _vertex_endpoint(self, project: str) -> str:
        return (
            f"https://{self.vertex_location}-aiplatform.googleapis.com/v1/"
            f"projects/{project}/locations/{self.vertex_location}/"
            f"publishers/google/models/{self.vertex_model}:generateContent"
        )

    def _request_vertex_json(self, session, endpoint: str, payload: dict) -> dict | None:
        return self._request_vertex_json_with_prompt(
            session=session,
            endpoint=endpoint,
            payload=payload,
            system_prompt=self._llm_system_prompt(),
            max_output_tokens=self.vertex_max_output_tokens,
            temperature=self.vertex_temperature,
        )

    def _request_vertex_json_with_prompt(
        self,
        session,
        endpoint: str,
        payload: dict,
        system_prompt: str,
        max_output_tokens: int,
        temperature: float,
    ) -> dict | None:
        request_body = {
            "systemInstruction": {
                "parts": [{"text": system_prompt}],
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": json.dumps(payload)}],
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
                "responseMimeType": "application/json",
            },
        }
        response = session.post(
            endpoint,
            json=request_body,
            timeout=self.vertex_timeout_seconds,
        )
        response.raise_for_status()
        raw = response.json()
        text = self._extract_vertex_text(raw)
        if not text:
            logger.warning("Vertex response had no text candidates endpoint=%s", endpoint)
            return None
        parsed = self._json_from_text(text)
        if parsed is None:
            sample = text.strip().replace("\n", " ")[:240]
            logger.warning(
                "Vertex response was not valid JSON endpoint=%s sample=%r",
                endpoint,
                sample,
            )
        return parsed

    def generate_meme(
        self,
        bucket: str,
        reason: str,
        repo_name: str,
        pr_number: int,
    ) -> dict | None:
        if self.ai_provider not in {"vertex", "auto"}:
            return None
        if not self.vertex_model or not self.vertex_project:
            return None

        try:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession
        except Exception:
            return None

        try:
            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            session = AuthorizedSession(credentials)
            endpoint = self._vertex_endpoint(self.vertex_project)
            payload = {
                "task": "generate short meme-style response for a GitHub PR event",
                "bucket": bucket,
                "reason": reason,
                "repo": repo_name,
                "pr_number": pr_number,
                "format": {
                    "title": "string",
                    "setup": "string",
                    "punchline": "string",
                },
            }
            data = self._request_vertex_json_with_prompt(
                session=session,
                endpoint=endpoint,
                payload=payload,
                system_prompt=(
                    "You are a witty GitHub bot. "
                    "Return only JSON with keys: title, setup, punchline. "
                    "Keep each field short and clean for markdown. "
                    "No URLs, no markdown code fences, no emojis."
                ),
                max_output_tokens=220,
                temperature=0.9,
            )
            if not isinstance(data, dict):
                return None
            title = str(data.get("title", "")).strip()
            setup = str(data.get("setup", "")).strip()
            punchline = str(data.get("punchline", "")).strip()
            if not title or not setup or not punchline:
                return None
            return {
                "title": title[:120],
                "setup": setup[:220],
                "punchline": punchline[:220],
            }
        except Exception as exc:
            logger.debug("AI meme generation failed bucket=%s error=%s", bucket, exc)
            return None

    def _merge_vertex_batch_payloads(
        self,
        payloads: list[dict],
        total_files: int,
        batch_count: int,
    ) -> dict:
        if not payloads:
            return {
                "summary": "Vertex review generated no batch results.",
                "findings": [],
                "risk_score": 100,
                "approve": False,
                "confidence": 0.0,
            }
        summaries: list[str] = []
        findings: list[dict] = []
        risk_scores: list[int] = []
        approvals: list[bool] = []
        confidences: list[float] = []
        for item in payloads:
            if not isinstance(item, dict):
                continue
            summary = str(item.get("summary", "")).strip()
            if summary:
                summaries.append(summary)
            raw_findings = item.get("findings", [])
            if isinstance(raw_findings, list):
                for raw in raw_findings:
                    if isinstance(raw, dict):
                        findings.append(raw)
            try:
                risk_scores.append(int(item.get("risk_score", 50)))
            except Exception:
                risk_scores.append(50)
            approvals.append(bool(item.get("approve", False)))
            try:
                confidences.append(float(item.get("confidence", 0.5)))
            except Exception:
                confidences.append(0.5)

        merged_risk = max(0, min(100, int(sum(risk_scores) / max(1, len(risk_scores)))))
        merged_confidence = max(0.0, min(1.0, min(confidences) if confidences else 0.5))
        merged_approve = all(approvals) if approvals else False
        summary_text = (
            f"Vertex review completed across {total_files} file(s) in {batch_count} batch(es)."
        )
        if summaries:
            summary_text = summary_text + " " + " ".join(summaries[:2])
        return {
            "summary": summary_text,
            "findings": findings,
            "risk_score": merged_risk,
            "approve": merged_approve,
            "confidence": merged_confidence,
        }

    @staticmethod
    def _extract_vertex_text(payload: dict) -> str:
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            return ""
        for item in candidates:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, dict):
                continue
            parts = content.get("parts")
            if not isinstance(parts, list):
                continue
            text_parts: list[str] = []
            for part in parts:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    text_parts.append(text)
            if text_parts:
                return "".join(text_parts).strip()
        return ""

    @staticmethod
    def _llm_system_prompt() -> str:
        return (
            "You are a strict GitHub code review bot. "
            "Return only valid JSON matching output_format. "
            "Focus on concrete risks in changed code and avoid generic advice."
        )

    def _llm_request_payload(
        self,
        title: str,
        description: str,
        changed_files: list[ChangedFile],
        profile: str,
        checks: set[str],
        total_files_changed: int | None = None,
        batch_index: int | None = None,
        batch_count: int | None = None,
    ) -> dict:
        files = changed_files

        return {
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
                    "patch": f.patch,
                }
                for f in files
            ],
            "llm_input": {
                "total_files_changed": total_files_changed if total_files_changed is not None else len(changed_files),
                "files_sent_to_model": len(files),
                "batch_index": batch_index or 1,
                "batch_count": batch_count or 1,
                "patch_chars_per_file": "full",
            },
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

    @staticmethod
    def _json_from_text(text: str) -> dict | None:
        payload = (text or "").strip()
        if not payload:
            return None
        if payload.startswith("```"):
            start = payload.find("{")
            end = payload.rfind("}")
            if start != -1 and end != -1 and end >= start:
                payload = payload[start : end + 1]
        try:
            value = json.loads(payload)
            if isinstance(value, dict):
                return value
        except Exception:
            pass
        candidate = BotIntelligence._first_json_object(payload)
        if candidate is None:
            return None
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except Exception:
            return None
        return None

    @staticmethod
    def _first_json_object(text: str) -> str | None:
        start = text.find("{")
        if start < 0:
            return None
        depth = 0
        in_string = False
        escaped = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : idx + 1]
        return None

    def _review_result_from_llm_data(
        self,
        data: dict,
        profile: str,
        checks: set[str],
        source: str,
    ) -> ReviewResult:
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
            source=source,
            risk_score=risk_score,
            check_results=check_results,
            suggestions=sorted(set(suggestions)),
            profile=profile,
        )

    def _llm_unavailable_result(self, profile: str, checks: set[str]) -> ReviewResult:
        findings = self._assign_finding_ids(
            [
                self._new_finding(
                    severity="high",
                    category="quality",
                    file="*",
                    message="LLM review is required but unavailable.",
                    suggestion=(
                        "Verify AI provider configuration and credentials "
                        "(for Vertex AI: GOOGLE_CLOUD_PROJECT, VERTEX_LOCATION, "
                        "VERTEX_MODEL, and service account access)."
                    ),
                )
            ]
        )
        return ReviewResult(
            summary="LLM-required mode is enabled, but the model request failed.",
            findings=findings,
            approve=False,
            confidence=0.0,
            source="llm-unavailable",
            risk_score=100,
            check_results={name: False for name in checks},
            suggestions=[findings[0].suggestion],
            profile=profile,
        )

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
