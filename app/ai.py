import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ChangedFile:
    filename: str
    status: str
    additions: int
    deletions: int
    patch: str


@dataclass(frozen=True)
class ReviewResult:
    summary: str
    findings: list[str]
    approve: bool
    confidence: float
    source: str


class BotIntelligence:
    def __init__(self, openai_api_key: str | None, model: str) -> None:
        self.openai_api_key = openai_api_key
        self.model = model

    def review_pull_request(
        self, title: str, description: str, changed_files: list[ChangedFile]
    ) -> ReviewResult:
        if self.openai_api_key:
            llm_result = self._review_with_openai(title, description, changed_files)
            if llm_result is not None:
                return llm_result
        return self._review_with_rules(title, changed_files)

    def _review_with_openai(
        self, title: str, description: str, changed_files: list[ChangedFile]
    ) -> ReviewResult | None:
        try:
            from openai import OpenAI
        except Exception as exc:
            return None

        client = OpenAI(api_key=self.openai_api_key)
        payload = {
            "title": title,
            "description": description[:4000],
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
                "findings": ["string"],
                "approve": "boolean",
                "confidence": "number between 0 and 1",
            },
        }
        prompt = (
            "You are a strict code reviewer for a GitHub bot. "
            "Review this PR data and return ONLY JSON matching output_format. "
            "Prefer safety over speed. Reject if you find significant risk."
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
            findings = [str(item) for item in data.get("findings", [])]
            confidence = float(data.get("confidence", 0.5))
            confidence = min(max(confidence, 0.0), 1.0)
            return ReviewResult(
                summary=str(data.get("summary", "LLM review generated.")),
                findings=findings,
                approve=bool(data.get("approve", False)),
                confidence=confidence,
                source="openai",
            )
        except Exception as exc:
            return None

    def _review_with_rules(self, title: str, changed_files: list[ChangedFile]) -> ReviewResult:
        findings: list[str] = []
        risky_patterns = [
            ("TRACKED_TASK", "Found TRACKED_TASK markers in changed code."),
            ("TRACKED_TASK", "Found TRACKED_TASK markers in changed code."),
            ("console.log(", "Debug logging found (console.log)."),
            ("print(", "Debug logging found (print)."),
            ("password", "Potential credential handling detected."),
            ("api_key", "Potential secret handling detected."),
            ("except Exception", "Broad exception catch detected."),
            ("eval(", "Use of eval detected."),
        ]

        total_delta = sum(f.additions + f.deletions for f in changed_files)
        if len(changed_files) > 20:
            findings.append("Large PR: more than 20 files changed.")
        if total_delta > 1200:
            findings.append("Large PR: more than 1200 total line changes.")

        has_test_change = any(
            f.filename.startswith("tests/")
            or "/test" in f.filename.lower()
            or f.filename.lower().endswith("_test.py")
            for f in changed_files
        )
        has_src_change = any(
            f.filename.endswith(".py")
            or f.filename.endswith(".js")
            or f.filename.endswith(".ts")
            for f in changed_files
        )
        if has_src_change and not has_test_change:
            findings.append("Source code changed without matching test changes.")

        for file in changed_files:
            patch = file.patch or ""
            for token, message in risky_patterns:
                if token in patch:
                    findings.append(f"{message} File: {file.filename}")

        unique_findings = sorted(set(findings))
        approve = len(unique_findings) == 0
        confidence = 0.90 if approve else 0.55
        summary = (
            f"Rule-based review for PR '{title}': "
            f"{'no blocking issues found' if approve else 'potential risks found'}."
        )
        return ReviewResult(
            summary=summary,
            findings=unique_findings,
            approve=approve,
            confidence=confidence,
            source="rules",
        )

