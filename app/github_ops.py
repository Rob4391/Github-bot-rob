from github.GithubException import UnknownObjectException

from .ai import ChangedFile
from .auth import GitHubClientFactory
from .semver import bump_semver, parse_semver


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
            if idx >= max_files:
                break
            patch = (file.patch or "")[:max_patch_chars]
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
