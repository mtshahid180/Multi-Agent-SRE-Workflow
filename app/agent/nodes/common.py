"""Helpers shared by graph nodes."""

from app.agent.state import Deps
from app.integrations.llm_client import BudgetedLLM
from app.schemas.incident import Incident, IncidentStatus
from app.security.redaction import redact

ACTOR = "agent"


def budgeted_llm(deps: Deps, incident: Incident) -> BudgetedLLM:
    return BudgetedLLM(deps.llm, deps.settings.max_llm_calls_per_incident, incident.llm_calls)


def record_llm_usage(deps: Deps, incident_id: str, llm: BudgetedLLM) -> None:
    def mutate(inc: Incident) -> None:
        inc.llm_calls = llm.used

    deps.store.update(incident_id, mutate)


def set_status(deps: Deps, incident_id: str, status: IncidentStatus, **detail) -> Incident:
    def mutate(inc: Incident) -> None:
        inc.status = status

    incident = deps.store.update(incident_id, mutate)
    deps.store.add_event(incident_id, ACTOR, f"status:{status.value}", detail)
    return incident


def fail(deps: Deps, incident_id: str, message: str) -> dict:
    message = redact(message)[:500]

    def mutate(inc: Incident) -> None:
        inc.status = IncidentStatus.FAILED
        inc.error = message

    deps.store.update(incident_id, mutate)
    deps.store.add_event(incident_id, ACTOR, "failed", {"error": message})
    return {"outcome": "stop"}


def note(deps: Deps, incident_id: str, text: str) -> None:
    def mutate(inc: Incident) -> None:
        inc.notes.append(text)

    deps.store.update(incident_id, mutate)
