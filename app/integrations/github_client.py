"""GitHub REST client (GitHub App or scoped token) and a read-only local repo source."""

import contextlib
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import httpx
import jwt

from app.config import Settings


class GitHubError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class RepoSource(Protocol):
    supports_delivery: bool

    def default_branch(self, repo: str) -> str: ...
    def resolve_ref(self, repo: str, ref: str) -> str: ...
    def get_file(self, repo: str, path: str, ref: str) -> str | None: ...
    def recent_commits(self, repo: str, path: str, ref: str, limit: int = 5) -> list[dict]: ...
    # delivery
    def push_branch(
        self, repo: str, branch: str, base_sha: str, files: dict[str, str], message: str
    ) -> str: ...
    def dispatch_workflow(self, repo: str, workflow: str, branch: str) -> None: ...
    def find_run(self, repo: str, workflow: str, branch: str, since: datetime) -> dict | None: ...
    def get_run(self, repo: str, run_id: int) -> dict: ...
    def get_failed_jobs(self, repo: str, run_id: int) -> list[dict]: ...
    def create_pull_request(
        self, repo: str, title: str, body: str, head: str, base: str, draft: bool
    ) -> str: ...


class GitHubClient:
    supports_delivery = True

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self._s = settings
        self._http = httpx.Client(
            base_url=settings.github_api_url,
            timeout=30.0,
            follow_redirects=True,
            transport=transport,
        )
        self._tokens: dict[str, tuple[str, float]] = {}

    # -- auth ------------------------------------------------------------
    def _app_jwt(self) -> str:
        if not (self._s.github_app_id and self._s.github_private_key_path):
            raise GitHubError("GitHub credentials are not configured.")
        key = Path(self._s.github_private_key_path).read_text()
        now = int(time.time())
        return jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": self._s.github_app_id},
            key,
            algorithm="RS256",
        )

    def _token_for(self, repo: str) -> str:
        if self._s.github_token:
            return self._s.github_token
        cached = self._tokens.get(repo)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        headers = {
            "Authorization": f"Bearer {self._app_jwt()}",
            "Accept": "application/vnd.github+json",
        }
        inst = self._s.github_installation_id
        if not inst:
            r = self._http.get(f"/repos/{repo}/installation", headers=headers)
            self._check(r)
            inst = str(r.json()["id"])
        r = self._http.post(
            f"/app/installations/{inst}/access_tokens",
            headers=headers,
            json={"repositories": [repo.split("/")[1]]},
        )
        self._check(r)
        token = r.json()["token"]
        self._tokens[repo] = (token, time.time() + 3000)
        return token

    @staticmethod
    def _check(resp: httpx.Response, ok: tuple[int, ...] = (200, 201, 202, 204)) -> None:
        if resp.status_code not in ok:
            try:
                msg = resp.json().get("message", "")
            except ValueError:
                msg = ""
            raise GitHubError(f"GitHub API {resp.status_code}: {msg[:200]}", resp.status_code)

    def _req(
        self,
        method: str,
        repo: str,
        path: str,
        *,
        accept: str | None = None,
        ok: tuple[int, ...] = (200, 201, 202, 204),
        **kw,
    ) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self._token_for(repo)}",
            "Accept": accept or "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            resp = self._http.request(method, path, headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub request failed: {type(exc).__name__}") from exc
        self._check(resp, ok)
        return resp

    # -- read ------------------------------------------------------------
    def default_branch(self, repo: str) -> str:
        return self._req("GET", repo, f"/repos/{repo}").json()["default_branch"]

    def resolve_ref(self, repo: str, ref: str) -> str:
        return self._req("GET", repo, f"/repos/{repo}/commits/{ref}").json()["sha"]

    def get_file(self, repo: str, path: str, ref: str) -> str | None:
        r = self._req(
            "GET",
            repo,
            f"/repos/{repo}/contents/{path}",
            params={"ref": ref},
            accept="application/vnd.github.raw+json",
            ok=(200, 404),
        )
        return None if r.status_code == 404 else r.text

    def recent_commits(self, repo: str, path: str, ref: str, limit: int = 5) -> list[dict]:
        r = self._req(
            "GET",
            repo,
            f"/repos/{repo}/commits",
            params={"sha": ref, "path": path, "per_page": limit},
        )
        return [
            {
                "sha": c["sha"][:7],
                "message": c["commit"]["message"].splitlines()[0][:200],
                "date": c["commit"]["author"]["date"],
            }
            for c in r.json()
        ]

    # -- delivery ----------------------------------------------------------
    def push_branch(
        self, repo: str, branch: str, base_sha: str, files: dict[str, str], message: str
    ) -> str:
        base_tree = self._req("GET", repo, f"/repos/{repo}/git/commits/{base_sha}").json()
        tree = self._req(
            "POST",
            repo,
            f"/repos/{repo}/git/trees",
            json={
                "base_tree": base_tree["tree"]["sha"],
                "tree": [
                    {"path": p, "mode": "100644", "type": "blob", "content": c}
                    for p, c in files.items()
                ],
            },
        ).json()
        commit = self._req(
            "POST",
            repo,
            f"/repos/{repo}/git/commits",
            json={"message": message, "tree": tree["sha"], "parents": [base_sha]},
        ).json()
        self._req(
            "POST",
            repo,
            f"/repos/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
        )
        return commit["sha"]

    def dispatch_workflow(self, repo: str, workflow: str, branch: str) -> None:
        self._req(
            "POST",
            repo,
            f"/repos/{repo}/actions/workflows/{workflow}/dispatches",
            json={"ref": branch},
        )

    def find_run(self, repo: str, workflow: str, branch: str, since: datetime) -> dict | None:
        r = self._req(
            "GET",
            repo,
            f"/repos/{repo}/actions/workflows/{workflow}/runs",
            params={"branch": branch, "event": "workflow_dispatch", "per_page": 5},
        )
        for run in r.json().get("workflow_runs", []):
            created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
            if created.astimezone(UTC) >= since.astimezone(UTC).replace(microsecond=0):
                return run
        return None

    def get_run(self, repo: str, run_id: int) -> dict:
        return self._req("GET", repo, f"/repos/{repo}/actions/runs/{run_id}").json()

    def get_failed_jobs(self, repo: str, run_id: int) -> list[dict]:
        jobs = self._req("GET", repo, f"/repos/{repo}/actions/runs/{run_id}/jobs").json()["jobs"]
        failed = []
        for job in jobs:
            if job.get("conclusion") in ("failure", "timed_out", "cancelled"):
                steps = [
                    s["name"] for s in job.get("steps", []) if s.get("conclusion") == "failure"
                ]
                log = ""
                with contextlib.suppress(GitHubError):
                    log = self._req(
                        "GET", repo, f"/repos/{repo}/actions/jobs/{job['id']}/logs", ok=(200,)
                    ).text[-6000:]
                failed.append({"name": job["name"], "failed_steps": steps, "log": log})
        return failed

    def create_pull_request(
        self, repo: str, title: str, body: str, head: str, base: str, draft: bool
    ) -> str:
        r = self._req(
            "POST",
            repo,
            f"/repos/{repo}/pulls",
            json={"title": title, "body": body, "head": head, "base": base, "draft": draft},
        )
        return r.json()["html_url"]


class LocalRepoSource:
    """Read-only access to a sample repository under LOCAL_REPOS_DIR (no delivery)."""

    supports_delivery = False

    def __init__(self, root: str):
        self._root = Path(root).resolve()

    def _dir(self, repo: str) -> Path:
        d = (self._root / repo.removeprefix("local:")).resolve()
        if self._root not in d.parents or not d.is_dir():
            raise GitHubError(f"Local repository not found: {repo}")
        return d

    def default_branch(self, repo: str) -> str:
        return "local"

    def resolve_ref(self, repo: str, ref: str) -> str:
        self._dir(repo)
        return "local"

    def get_file(self, repo: str, path: str, ref: str) -> str | None:
        base = self._dir(repo)
        f = (base / path).resolve()
        if base not in f.parents or not f.is_file():
            return None
        try:
            return f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None

    def recent_commits(self, repo: str, path: str, ref: str, limit: int = 5) -> list[dict]:
        return []

    def _no_delivery(self, *a, **k):
        raise GitHubError("Delivery is not supported for local repositories.")

    push_branch = dispatch_workflow = find_run = get_run = _no_delivery
    get_failed_jobs = create_pull_request = _no_delivery


class RepoRouter:
    """Chooses the right RepoSource for an incident's repository string."""

    def __init__(self, github: RepoSource, local: RepoSource):
        self._github, self._local = github, local

    def get(self, repository: str) -> RepoSource:
        return self._local if repository.startswith("local:") else self._github
