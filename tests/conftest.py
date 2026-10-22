import pytest
from fastapi.testclient import TestClient

from app.agent.state import Deps
from app.config import Settings
from app.integrations.github_client import GitHubError, LocalRepoSource, RepoRouter
from app.integrations.llm_client import LLMError
from app.integrations.test_runner import CIRunner
from app.main import create_app
from app.storage.incidents import IncidentStore

BUGGY = "def average(values):\n    total = sum(values)\n    return total / len(values)\n"
TRACE = (
    "Traceback (most recent call last):\n"
    '  File "/srv/app/shop/stats.py", line 3, in average\n'
    "    return total / len(values)\n"
    "ZeroDivisionError: division by zero\n"
)
GOOD_EDIT = {
    "can_fix": True,
    "summary": "Return 0 for empty input",
    "rationale": "len(values) is 0",
    "edits": [
        {
            "path": "shop/stats.py",
            "search": "    return total / len(values)",
            "replace": "    return total / len(values) if values else 0",
        }
    ],
    "tests_to_run": ["tests/test_stats.py"],
    "risk": "low",
}


class FakeLLM:
    """Scripted model: answers root-cause prompts and pops patch answers in order."""

    def __init__(self, patches: list[dict] | None = None, fail: bool = False):
        self.patches = list(patches if patches is not None else [GOOD_EDIT])
        self.fail = fail
        self.calls = 0

    def complete_json(self, system: str, user: str) -> dict:
        self.calls += 1
        if self.fail:
            raise LLMError("boom")
        if "Analyse the incident" in user:
            return {
                "summary": "average() divides by zero for empty input",
                "hypotheses": [
                    {
                        "description": "Empty list leads to division by zero",
                        "supporting_evidence": ["E1", "E999"],
                        "assumptions": ["Empty lists are valid input"],
                        "confidence": "high",
                    }
                ],
            }
        return self.patches.pop(0)


class FakeRepo:
    supports_delivery = True

    def __init__(self, files: dict[str, str] | None = None, ci: list[str] | None = None):
        self.files = files if files is not None else {"shop/stats.py": BUGGY}
        self.ci = list(ci if ci is not None else ["success"])
        self.pushed: list[tuple[str, dict]] = []
        self.pulls: list[dict] = []
        self.dispatched: list[str] = []

    def default_branch(self, repo):
        return "main"

    def resolve_ref(self, repo, ref):
        return "a" * 40

    def get_file(self, repo, path, ref):
        return self.files.get(path)

    def recent_commits(self, repo, path, ref, limit=5):
        return [{"sha": "abc1234", "date": "2026-10-01", "message": "refactor stats"}]

    def push_branch(self, repo, branch, base_sha, files, message):
        self.pushed.append((branch, files))
        return "c" * 40

    def dispatch_workflow(self, repo, workflow, branch):
        self.dispatched.append(branch)

    def find_run(self, repo, workflow, branch, since):
        return {
            "id": len(self.dispatched),
            "status": "completed",
            "conclusion": self.ci.pop(0),
            "html_url": "https://ci/run",
        }

    def get_run(self, repo, run_id):
        raise GitHubError("unused")

    def get_failed_jobs(self, repo, run_id):
        return [
            {
                "name": "verify",
                "failed_steps": ["Test (pytest)"],
                "log": "FAILED test_x token=ghp_" + "a" * 30,
            }
        ]

    def create_pull_request(self, repo, title, body, head, base, draft):
        self.pulls.append(
            {"title": title, "body": body, "head": head, "base": base, "draft": draft}
        )
        return "https://github.com/acme/shop/pull/7"


@pytest.fixture
def make_env(tmp_path):
    def _make(llm=None, repo=None, **overrides):
        settings = Settings(
            database_url=f"sqlite:///{tmp_path}/t.db",
            api_key="k",
            allowed_repos="acme/shop,local:sample_app",
            local_repos_dir="examples",
            github_webhook_secret="whsec",
            ci_poll_interval_s=0,
            **overrides,
        )
        repo = repo or FakeRepo()
        deps = Deps(
            settings=settings,
            store=IncidentStore(settings.database_url),
            llm=llm or FakeLLM(),
            repos=RepoRouter(repo, LocalRepoSource("examples")),
            ci=CIRunner(settings, sleep=lambda s: None),
        )
        client = TestClient(create_app(settings, deps), headers={"X-API-Key": "k"})
        return client, repo, deps

    return _make


@pytest.fixture
def incident_body():
    return {
        "title": "Checkout 500s",
        "description": "empty carts",
        "repository": "acme/shop",
        "logs": TRACE,
    }
