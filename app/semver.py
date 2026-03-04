import re
from dataclasses import dataclass


SEMVER_PATTERN = re.compile(r"^\s*(\d+)\.(\d+)\.(\d+)\s*$")


@dataclass(frozen=True)
class SemVer:
    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def parse_semver(value: str) -> SemVer:
    match = SEMVER_PATTERN.match(value)
    if not match:
        raise ValueError(f"Invalid semantic version: {value!r}")
    major, minor, patch = map(int, match.groups())
    return SemVer(major=major, minor=minor, patch=patch)


def bump_semver(version: str, bump_type: str) -> str:
    semver = parse_semver(version)
    normalized = bump_type.lower().strip()
    if normalized == "major":
        return str(SemVer(major=semver.major + 1, minor=0, patch=0))
    if normalized == "minor":
        return str(SemVer(major=semver.major, minor=semver.minor + 1, patch=0))
    if normalized == "patch":
        return str(
            SemVer(major=semver.major, minor=semver.minor, patch=semver.patch + 1)
        )
    raise ValueError(f"Unsupported bump type: {bump_type!r}")

