"""FastAPI entrypoint. Run with:  uvicorn app.main:create_app --factory --reload"""

import logging

from fastapi import FastAPI

from app.agent.runner import AgentRunner
from app.agent.state import Deps
from app.api import routes_approvals, routes_incidents, webhooks
from app.config import Settings, get_settings
from app.integrations.github_client import GitHubClient, LocalRepoSource, RepoRouter
from app.integrations.llm_client import build_llm
from app.integrations.test_runner import CIRunner
from app.storage.incidents import IncidentStore

log = logging.getLogger("ai_sre_agent")


def create_app(settings: Settings | None = None, deps: Deps | None = None) -> FastAPI:
    settings = settings or get_settings()
    if not settings.is_development and not settings.api_key:
        raise RuntimeError("API_KEY must be set when APP_ENV is not 'development'.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if not settings.api_key:
        log.warning("API_KEY is not set: API authentication is DISABLED (development only).")
    if not settings.llm_api_key:
        log.warning("LLM_API_KEY is not set: only heuristic analysis is available.")

    if deps is None:
        store = IncidentStore(settings.database_url)
        deps = Deps(
            settings=settings,
            store=store,
            llm=build_llm(settings),
            repos=RepoRouter(GitHubClient(settings), LocalRepoSource(settings.local_repos_dir)),
            ci=CIRunner(settings),
        )
    app = FastAPI(title="AI SRE Agent", version="0.1.0")
    app.state.settings, app.state.store = settings, deps.store
    app.state.runner = AgentRunner(deps)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "env": settings.app_env}

    app.include_router(routes_incidents.router)
    app.include_router(routes_approvals.router)
    app.include_router(webhooks.router)
    return app
