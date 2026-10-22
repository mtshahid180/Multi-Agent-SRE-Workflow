from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.agent.runner import AgentRunner
from app.api.deps import get_runner, require_api_key
from app.schemas.incident import Incident
from app.security import approvals
from app.storage.incidents import IncidentNotFoundError

router = APIRouter(dependencies=[Depends(require_api_key)])


class ApprovalRequest(BaseModel):
    approver: str = Field(min_length=1, max_length=100)
    patch_sha256: str = Field(min_length=64, max_length=64)
    comment: str = Field(default="", max_length=1000)


class RejectRequest(BaseModel):
    approver: str = Field(min_length=1, max_length=100)
    comment: str = Field(default="", max_length=1000)


def _call(fn, *args) -> Incident:
    try:
        return fn(*args)
    except IncidentNotFoundError:
        raise HTTPException(404, "Incident not found.") from None
    except approvals.ApprovalError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None


@router.post("/incidents/{incident_id}/approve", status_code=202)
def approve(
    incident_id: str,
    body: ApprovalRequest,
    background: BackgroundTasks,
    request: Request,
    runner: AgentRunner = Depends(get_runner),
) -> dict:
    incident = _call(
        approvals.approve,
        request.app.state.store,
        incident_id,
        body.approver,
        body.patch_sha256,
        body.comment,
    )
    background.add_task(runner.run, incident.id)
    return {"id": incident.id, "status": incident.status}


@router.post("/incidents/{incident_id}/reject")
def reject(incident_id: str, body: RejectRequest, request: Request) -> dict:
    incident = _call(
        approvals.reject, request.app.state.store, incident_id, body.approver, body.comment
    )
    return {"id": incident.id, "status": incident.status}
