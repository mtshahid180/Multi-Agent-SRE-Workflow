from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from app.agent.runner import AgentRunner
from app.api.deps import get_runner, get_settings_dep, require_api_key
from app.config import Settings
from app.schemas.incident import Incident, IncidentCreate
from app.security.policies import is_repository_allowed
from app.security.redaction import redact
from app.storage.incidents import IncidentNotFoundError, IncidentStore

router = APIRouter(dependencies=[Depends(require_api_key)])


def _store(request: Request) -> IncidentStore:
    return request.app.state.store


def _get_or_404(store: IncidentStore, incident_id: str) -> Incident:
    try:
        return store.get(incident_id)
    except IncidentNotFoundError:
        raise HTTPException(404, "Incident not found.") from None


def create_incident(
    body: IncidentCreate, store: IncidentStore, settings: Settings, actor: str
) -> Incident:
    if not is_repository_allowed(body.repository, settings):
        raise HTTPException(403, f"Repository '{body.repository}' is not in ALLOWED_REPOS.")
    body = body.model_copy(
        update={
            "logs": redact(body.logs),
            "test_output": redact(body.test_output),
            "description": redact(body.description),
        }
    )
    incident = store.create(Incident(request=body))
    store.add_event(incident.id, actor, "incident_received", {"repository": body.repository})
    return incident


@router.post("/incidents", status_code=202)
def submit_incident(
    body: IncidentCreate,
    background: BackgroundTasks,
    request: Request,
    runner: AgentRunner = Depends(get_runner),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    incident = create_incident(body, _store(request), settings, "api")
    background.add_task(runner.run, incident.id)
    return {"id": incident.id, "status": incident.status}


@router.get("/incidents")
def list_incidents(request: Request, limit: int = 50) -> list[dict]:
    return [
        {
            "id": i.id,
            "status": i.status,
            "title": i.request.title,
            "repository": i.request.repository,
            "updated_at": i.updated_at,
        }
        for i in _store(request).recent(min(max(limit, 1), 200))
    ]


@router.get("/incidents/{incident_id}", response_model=Incident)
def get_incident(incident_id: str, request: Request) -> Incident:
    return _get_or_404(_store(request), incident_id)


@router.get("/incidents/{incident_id}/patch")
def get_patch(incident_id: str, request: Request) -> dict:
    incident = _get_or_404(_store(request), incident_id)
    if incident.patch is None:
        raise HTTPException(404, "No patch has been proposed for this incident.")
    return {"patch": incident.patch, "policy": incident.policy, "status": incident.status}


@router.get("/incidents/{incident_id}/events")
def get_events(incident_id: str, request: Request) -> list[dict]:
    _get_or_404(_store(request), incident_id)
    return _store(request).events(incident_id)
