"""Remote verification through the predefined GitHub Actions workflow, with bounded retries."""

from app.agent.nodes.common import ACTOR, fail, set_status
from app.agent.state import AgentState, Deps
from app.schemas.incident import Incident, IncidentStatus


def run(state: AgentState, deps: Deps) -> dict:
    incident_id = state["incident_id"]
    incident = deps.store.get(incident_id)
    set_status(deps, incident_id, IncidentStatus.VERIFYING, branch=incident.branch)
    req = incident.request
    result = deps.ci.run(
        deps.repos.get(req.repository), req.repository, incident.branch or "", incident.attempts
    )

    def mutate(inc: Incident) -> None:
        inc.verifications.append(result)

    deps.store.update(incident_id, mutate)
    deps.store.add_event(
        incident_id,
        ACTOR,
        "verification_finished",
        {"passed": result.passed, "conclusion": result.conclusion, "run_url": result.run_url},
    )
    if result.passed:
        return {"outcome": "passed"}
    if incident.attempts < deps.settings.max_fix_attempts:
        return {"outcome": "retry"}
    return fail(
        deps,
        incident_id,
        f"Verification failed after {incident.attempts} attempt(s) "
        f"({result.conclusion}). No Pull Request was created.",
    ) | {"outcome": "failed"}
