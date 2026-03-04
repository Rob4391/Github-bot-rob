from github import Github, GithubIntegration

from .config import Settings

try:
    from github import Auth
except Exception:  # pragma: no cover - compatibility fallback
    Auth = None


class GitHubClientFactory:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._integration = None
        self._app_auth = None

        if settings.auth_mode == "app":
            if settings.github_app_id is None or settings.github_app_private_key is None:
                raise RuntimeError("GitHub App mode requires app_id and private_key")

            if Auth is not None:
                self._app_auth = Auth.AppAuth(
                    app_id=settings.github_app_id,
                    private_key=settings.github_app_private_key,
                )
                self._integration = GithubIntegration(auth=self._app_auth)
            else:
                # Older PyGithub compatibility path.
                self._integration = GithubIntegration(
                    settings.github_app_id, settings.github_app_private_key
                )

    def get_client(self, installation_id: int | None) -> Github:
        if self.settings.auth_mode == "token":
            if not self.settings.github_token:
                raise RuntimeError("Token mode selected but GITHUB_TOKEN is empty")
            return Github(self.settings.github_token)

        if installation_id is None:
            raise RuntimeError(
                "Missing installation id in webhook payload for GitHub App authentication."
            )

        if self._app_auth is not None:
            installation_auth = self._app_auth.get_installation_auth(installation_id)
            return Github(auth=installation_auth)

        # Fallback for legacy PyGithub versions.
        token = self._integration.get_access_token(installation_id).token
        return Github(token)
