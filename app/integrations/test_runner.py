"""Triggers the predefined verification workflow remotely and interprets the result."""

import time
from collections.abc import Callable
from datetime import UTC, datetime

from app.config import Settings
from app.integrations.github_client import GitHubError, RepoSource
from app.schemas.incident import VerificationResult
from app.security.redaction import redact


class CIRunner:
    def __init__(self, settings: Settings, sleep: Callable[[float], None] = time.sleep):
        self._s = settings
        self._sleep = sleep

    def run(self, source: RepoSource, repo: str, branch: str, attempt: int) -> VerificationResult:
        since = datetime.now(UTC)
        try:
            source.dispatch_workflow(repo, self._s.verify_workflow_file, branch)
            run = self._wait_for_run(source, repo, branch, since)
            if run is None:
                return VerificationResult(
                    attempt=attempt,
                    passed=False,
                    conclusion="not_started",
                    log_excerpt="CI run was not found after dispatch.",
                )
            run = self._wait_until_done(source, repo, run)
        except GitHubError as exc:
            return VerificationResult(
                attempt=attempt, passed=False, conclusion="error", log_excerpt=str(exc)
            )

        conclusion = run.get("conclusion") or run.get("status") or "unknown"
        if conclusion == "success":
            return VerificationResult(
                attempt=attempt, passed=True, conclusion=conclusion, run_url=run.get("html_url")
            )
        failed = []
        excerpt = ""
        if conclusion != "timed_out_by_agent":
            try:
                for job in source.get_failed_jobs(repo, run["id"]):
                    steps = ", ".join(job["failed_steps"]) or "unknown step"
                    failed.append(f"{job['name']} ({steps})")
                    excerpt += redact(job["log"])[-3000:] + "\n"
            except GitHubError:
                pass
        return VerificationResult(
            attempt=attempt,
            passed=False,
            conclusion=conclusion,
            run_url=run.get("html_url"),
            failed_jobs=failed,
            log_excerpt=excerpt[-4000:],
        )

    def _wait_for_run(
        self, source: RepoSource, repo: str, branch: str, since: datetime
    ) -> dict | None:
        for _ in range(12):
            run = source.find_run(repo, self._s.verify_workflow_file, branch, since)
            if run:
                return run
            self._sleep(5)
        return None

    def _wait_until_done(self, source: RepoSource, repo: str, run: dict) -> dict:
        deadline = time.monotonic() + self._s.ci_timeout_s
        while run.get("status") != "completed":
            if time.monotonic() > deadline:
                run = dict(run)
                run["conclusion"] = "timed_out_by_agent"
                return run
            self._sleep(self._s.ci_poll_interval_s)
            run = source.get_run(repo, run["id"])
        return run
