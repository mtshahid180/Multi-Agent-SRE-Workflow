import hashlib
import hmac
import json

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request

from app.agent.runner import AgentRunner
from app.api.deps import get_runner, get_settings_dep
from app.api.routes_incidents import create_incident
from app.config import Settings
from app.schemas.incident import IncidentCreate

router = APIRouter()


def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/webhooks/github")
async def github_webhook(
    request: Request,
    background: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str = Header(default=""),
    x_github_delivery: str | None = Header(default=None),
    runner: AgentRunner = Depends(get_runner),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    if not settings.github_webhook_secret:
        raise HTTPException(503, "Webhook secret is not configured.")
    body = await request.body()
    if not verify_signature(settings.github_webhook_secret, body, x_hub_signature_256):
        raise HTTPException(401, "Invalid webhook signature.")
    if not x_github_delivery:
        raise HTTPException(400, "Missing X-GitHub-Delivery header.")
    store = request.app.state.store
    if not store.record_delivery(x_github_delivery):  # replay protection
        return {"status": "duplicate"}
    if x_github_event == "ping":
        return {"status": "pong"}
    if x_github_event != "issues":
        return {"status": "ignored"}

    payload = json.loads(body)
    issue, repo = payload.get("issue", {}), payload.get("repository", {}).get("full_name", "")
    labels = {lb.get("name") for lb in issue.get("labels", [])}
    if payload.get("action") not in ("opened", "labeled") or settings.incident_label not in labels:
        return {"status": "ignored"}
    try:
        incident_req = IncidentCreate(
            title=(issue.get("title") or "GitHub issue")[:200],
            description=(issue.get("body") or "")[:10_000],
            repository=repo,
            logs=(issue.get("body") or ""),
        )
    except ValueError:
        raise HTTPException(422, "Webhook payload could not be converted to an incident.") from None
    incident = create_incident(incident_req, store, settings, "webhook:github")
    background.add_task(runner.run, incident.id)
    return {"status": "accepted", "id": incident.id}
