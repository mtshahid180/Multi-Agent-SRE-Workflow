import hmac
import logging

from fastapi import Depends, Header, HTTPException, Request

from app.agent.runner import AgentRunner
from app.config import Settings

log = logging.getLogger("ai_sre_agent")


def get_runner(request: Request) -> AgentRunner:
    return request.app.state.runner


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def require_api_key(
    x_api_key: str | None = Header(default=None), settings: Settings = Depends(get_settings_dep)
) -> None:
    if not settings.api_key:  # only reachable in development (enforced at startup)
        return
    if not x_api_key or not hmac.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key.")
