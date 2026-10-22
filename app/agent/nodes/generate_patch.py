"""Ask the model for a minimal patch, validate it in memory and compute the diff."""

from app.agent import prompts
from app.agent.nodes.common import (
    ACTOR,
    budgeted_llm,
    note,
    record_llm_usage,
    set_status,
)
from app.agent.patching import PatchApplyError, apply_edits, make_diff, patch_hash
from app.agent.state import AgentState, Deps
from app.integrations.github_client import GitHubError
from app.integrations.llm_client import LLMError
from app.schemas.incident import FileEdit, Incident, IncidentStatus, PatchProposal
from app.security.redaction import redact


def _build_prompt(incident: Incident, files: dict[str, str], error: str | None) -> str:
    hyp = incident.hypotheses[0]
    sections = [
        prompts.PATCH_TASK,
        f"<untrusted_incident>\nTitle: {incident.request.title}\n"
        f"Description: {incident.request.description}\n</untrusted_incident>",
        f"Top hypothesis ({hyp.confidence} confidence): {hyp.description}",
    ]
    sections += [f'<untrusted_file path="{p}">\n{c}\n</untrusted_file>' for p, c in files.items()]
    if incident.verifications and not incident.verifications[-1].passed:
        last = incident.verifications[-1]
        prev = incident.patch.diff if incident.patch else ""
        sections.append(
            "Your previous patch FAILED verification. Propose a corrected patch.\n"
            f"<untrusted_previous_patch>\n{prev}\n</untrusted_previous_patch>\n"
            f'<untrusted_ci_output failed_jobs="{", ".join(last.failed_jobs)}">\n'
            f"{last.log_excerpt}\n</untrusted_ci_output>"
        )
    if error:
        sections.append(f"Your last answer could not be applied: {error}. Fix the edits.")
    return "\n\n".join(sections)


def run(state: AgentState, deps: Deps) -> dict:
    incident_id = state["incident_id"]
    incident = deps.store.get(incident_id)
    if not incident.hypotheses:
        return {"outcome": "no_patch"}
    if incident.attempts > 0:
        set_status(deps, incident_id, IncidentStatus.INVESTIGATING, retry=incident.attempts + 1)

    req = incident.request
    source = deps.repos.get(req.repository)
    ref = incident.base_sha or incident.base_branch or ""
    files: dict[str, str] = {}
    try:
        for ev in incident.evidence:
            if ev.kind == "file" and ev.ref not in files:
                content = source.get_file(req.repository, ev.ref, ref)
                if content is not None:
                    files[ev.ref] = content
    except GitHubError as exc:
        note(deps, incident_id, f"Could not load files for patching: {exc}")
        set_status(deps, incident_id, IncidentStatus.ANALYZED)
        return {"outcome": "no_patch"}

    llm = budgeted_llm(deps, incident)
    error: str | None = None
    attempt_no = incident.attempts + 1
    patch: PatchProposal | None = None
    reason = ""
    try:
        for _ in range(2):  # one repair round if the edits do not apply cleanly
            data = llm.complete_json(prompts.SYSTEM, _build_prompt(incident, files, error))
            if not data.get("can_fix", False):
                reason = str(data.get("reason_if_not", "Model declined to propose a fix."))
                break
            edits = [FileEdit.model_validate(e) for e in data.get("edits", [])]
            try:
                known: dict[str, str | None] = dict(files)
                for e in edits:
                    if e.search == "" and e.path not in known:
                        known[e.path] = source.get_file(req.repository, e.path, ref)
                    elif e.path not in known:
                        raise PatchApplyError(f"{e.path}: not one of the provided files.")
                after = apply_edits(known, edits)
            except PatchApplyError as exc:
                error = str(exc)
                continue
            diff, changed = make_diff(known, after)
            risk = data.get("risk", "medium")
            patch = PatchProposal(
                attempt=attempt_no,
                summary=redact(str(data.get("summary", "")))[:500],
                rationale=redact(str(data.get("rationale", "")))[:2000],
                edits=edits,
                diff=diff,
                tests_to_run=[str(t) for t in data.get("tests_to_run", [])][:10],
                risk=risk if risk in ("low", "medium", "high") else "medium",
                sha256=patch_hash(edits),
                changed_lines=changed,
            )
            break
        else:
            reason = f"Edits could not be applied: {error}"
    except (LLMError, ValueError, GitHubError) as exc:
        reason = f"Patch generation unavailable: {exc}"
    record_llm_usage(deps, incident_id, llm)

    def mutate(inc: Incident) -> None:
        inc.attempts = attempt_no
        if patch:
            inc.patch, inc.approval, inc.policy = patch, None, None

    deps.store.update(incident_id, mutate)
    if patch is None:
        note(deps, incident_id, f"No patch proposed: {reason}")
        set_status(deps, incident_id, IncidentStatus.ANALYZED)
        return {"outcome": "no_patch"}
    deps.store.add_event(
        incident_id,
        ACTOR,
        "patch_proposed",
        {"attempt": attempt_no, "sha256": patch.sha256, "risk": patch.risk},
    )
    return {"outcome": "patch"}
