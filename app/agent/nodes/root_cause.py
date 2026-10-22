"""Turn evidence into evidence-backed root-cause hypotheses."""

import re

from app.agent import prompts
from app.agent.nodes.common import ACTOR, budgeted_llm, note, record_llm_usage
from app.agent.state import AgentState, Deps
from app.integrations.llm_client import LLMError
from app.schemas.incident import Hypothesis, Incident
from app.security.redaction import redact


def format_evidence(incident: Incident) -> str:
    return "\n\n".join(
        f"[{e.id}] kind={e.kind} ref={e.ref}\n{e.excerpt}" for e in incident.evidence
    )


def build_prompt(incident: Incident) -> str:
    r = incident.request
    return (
        f"{prompts.ROOT_CAUSE_TASK}\n\n"
        f"<untrusted_incident>\nTitle: {r.title}\nDescription: {r.description}\n"
        f"Repository: {r.repository}\n</untrusted_incident>\n\n"
        f"<untrusted_evidence>\n{format_evidence(incident)}\n</untrusted_evidence>"
    )


def heuristic_hypothesis(incident: Incident) -> tuple[str, list[Hypothesis]]:
    """Fallback when no LLM is available: report what the evidence literally shows."""
    log = next((e for e in incident.evidence if e.kind in ("log", "test")), None)
    err = None
    if log:
        for line in reversed(log.excerpt.splitlines()):
            if re.match(r"^\s*[\w.]*(Error|Exception)\b.*", line):
                err = line.strip()
                break
    files = [e for e in incident.evidence if e.kind == "file"]
    where = f" in {files[0].ref}" if files else ""
    desc = (
        f"The failure appears to originate{where}: {err}"
        if err
        else "Evidence collected, but no specific error could be identified without a model."
    )
    summary = "Heuristic analysis only (LLM unavailable). " + desc
    return summary, [
        Hypothesis(
            description=desc,
            supporting_evidence=[e.id for e in ([log] if log else []) + files[:1]],
            assumptions=["The first project frame in the stack trace is the faulty code."],
            additional_evidence_needed=["Model-assisted analysis of the surrounding code."],
            confidence="low",
        )
    ]


def run(state: AgentState, deps: Deps) -> dict:
    incident_id = state["incident_id"]
    incident = deps.store.get(incident_id)
    llm = budgeted_llm(deps, incident)
    try:
        data = llm.complete_json(prompts.SYSTEM, build_prompt(incident))
        hyps = [Hypothesis.model_validate(h) for h in data.get("hypotheses", [])][:3]
        if not hyps:
            raise LLMError("Model returned no hypotheses.")
        summary = redact(str(data.get("summary", "")))[:1500]
    except (LLMError, ValueError) as exc:
        summary, hyps = heuristic_hypothesis(incident)
        note(deps, incident_id, f"LLM analysis unavailable ({exc}); used heuristic analysis.")
    record_llm_usage(deps, incident_id, llm)

    valid_ids = {e.id for e in incident.evidence}
    for h in hyps:  # drop evidence ids the model invented
        h.supporting_evidence = [i for i in h.supporting_evidence if i in valid_ids]

    def mutate(inc: Incident) -> None:
        inc.root_cause_summary, inc.hypotheses = summary, hyps

    deps.store.update(incident_id, mutate)
    deps.store.add_event(incident_id, ACTOR, "hypotheses_generated", {"count": len(hyps)})
    return {"outcome": "ok"}
