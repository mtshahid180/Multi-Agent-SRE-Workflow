"""Human approval handling. An approval is bound to the exact patch hash."""

import hmac

from app.schemas.incident import ApprovalRecord, Incident, IncidentStatus
from app.storage.incidents import IncidentStore


class ApprovalError(Exception):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def approve(
    store: IncidentStore, incident_id: str, approver: str, patch_sha256: str, comment: str = ""
) -> Incident:
    def mutate(inc: Incident) -> None:
        if inc.status != IncidentStatus.AWAITING_APPROVAL or inc.patch is None:
            raise ApprovalError(f"Incident is '{inc.status}', not awaiting approval.")
        if inc.policy is None or not inc.policy.allowed:
            raise ApprovalError("Patch is blocked by policy and cannot be approved.")
        if not hmac.compare_digest(inc.patch.sha256, patch_sha256):
            raise ApprovalError("patch_sha256 does not match the current patch; review it again.")
        inc.approval = ApprovalRecord(approver=approver, patch_sha256=patch_sha256, comment=comment)
        inc.status = IncidentStatus.APPROVED

    incident = store.update(incident_id, mutate)
    store.add_event(
        incident_id, approver, "approved", {"patch_sha256": patch_sha256, "comment": comment}
    )
    return incident


def reject(store: IncidentStore, incident_id: str, approver: str, comment: str = "") -> Incident:
    def mutate(inc: Incident) -> None:
        if inc.status != IncidentStatus.AWAITING_APPROVAL:
            raise ApprovalError(f"Incident is '{inc.status}', not awaiting approval.")
        inc.status = IncidentStatus.REJECTED
        inc.notes.append(f"Rejected by {approver}: {comment}".strip())

    incident = store.update(incident_id, mutate)
    store.add_event(incident_id, approver, "rejected", {"comment": comment})
    return incident
