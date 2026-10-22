"""Policy and approval gate: nothing leaves the agent without passing through here."""

from app.agent.nodes.common import ACTOR, set_status
from app.agent.state import AgentState, Deps
from app.schemas.incident import ApprovalRecord, Incident, IncidentStatus
from app.security.policies import evaluate_patch


def run(state: AgentState, deps: Deps) -> dict:
    incident_id = state["incident_id"]
    incident = deps.store.get(incident_id)
    assert incident.patch is not None
    decision = evaluate_patch(incident.patch, deps.settings, incident.request.repository)

    def mutate(inc: Incident) -> None:
        inc.policy = decision
        if decision.allowed and not decision.requires_approval:
            inc.approval = ApprovalRecord(
                approver="policy:auto-approve-low-risk",
                patch_sha256=inc.patch.sha256,
                comment="Auto-approved by policy (low risk).",
            )

    deps.store.update(incident_id, mutate)
    deps.store.add_event(incident_id, ACTOR, "policy_decision", decision.model_dump())
    if not decision.allowed:
        set_status(deps, incident_id, IncidentStatus.DENIED)
        return {"outcome": "denied"}
    if decision.requires_approval:
        set_status(deps, incident_id, IncidentStatus.AWAITING_APPROVAL)
        return {"outcome": "await_approval"}
    set_status(deps, incident_id, IncidentStatus.APPROVED)
    return {"outcome": "approved"}
