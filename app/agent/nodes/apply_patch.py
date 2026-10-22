"""Create a repair branch (never the default branch) containing the approved patch."""

import hmac

from app.agent.nodes.common import ACTOR, fail, note, set_status
from app.agent.patching import PatchApplyError, apply_edits
from app.agent.state import AgentState, Deps
from app.integrations.github_client import GitHubError
from app.schemas.incident import Incident, IncidentStatus
from app.security.policies import evaluate_patch


def run(state: AgentState, deps: Deps) -> dict:
    incident_id = state["incident_id"]
    incident = deps.store.get(incident_id)
    patch, approval, req = incident.patch, incident.approval, incident.request

    # Defence in depth: re-verify approval, patch hash and policy right before acting.
    if (
        incident.status != IncidentStatus.APPROVED
        or patch is None
        or approval is None
        or not hmac.compare_digest(approval.patch_sha256, patch.sha256)
    ):
        return fail(deps, incident_id, "Refusing to deliver: approval does not match the patch.")
    if not evaluate_patch(patch, deps.settings, req.repository).allowed:
        return fail(deps, incident_id, "Refusing to deliver: patch violates policy.")

    source = deps.repos.get(req.repository)
    if not source.supports_delivery:
        note(
            deps,
            incident_id,
            "Approved. Local repositories are read-only: apply the diff manually (git apply).",
        )
        return {"outcome": "no_delivery"}

    set_status(deps, incident_id, IncidentStatus.DELIVERING)
    branch = f"sre-agent/{incident.id}-a{patch.attempt}"
    try:
        paths = {e.path for e in patch.edits}
        before = {p: source.get_file(req.repository, p, incident.base_sha or "") for p in paths}
        after = apply_edits(before, patch.edits)
        message = f"fix: {patch.summary or incident.request.title}\n\nIncident: {incident.id}"
        commit_sha = source.push_branch(
            req.repository, branch, incident.base_sha or "", after, message
        )
    except (PatchApplyError, GitHubError) as exc:
        return fail(deps, incident_id, f"Could not create repair branch: {exc}")

    def mutate(inc: Incident) -> None:
        inc.branch = branch

    deps.store.update(incident_id, mutate)
    deps.store.add_event(
        incident_id, ACTOR, "branch_created", {"branch": branch, "commit": commit_sha}
    )
    return {"outcome": "pushed"}
