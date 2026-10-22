"""Typed schemas for incidents, findings, patches, policy decisions and CI results."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
Risk = Literal["low", "medium", "high"]


def utcnow() -> datetime:
    return datetime.now(UTC)


class IncidentStatus(StrEnum):
    RECEIVED = "received"
    INVESTIGATING = "investigating"
    ANALYZED = "analyzed"  # analysis done, no patch proposed
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    DELIVERING = "delivering"
    VERIFYING = "verifying"
    PR_CREATED = "pr_created"
    DENIED = "denied"  # blocked by policy
    REJECTED = "rejected"  # rejected by a human
    FAILED = "failed"


REPO_PATTERN = r"^(local:[A-Za-z0-9_.-]+|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)$"


class IncidentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10_000)
    repository: str = Field(pattern=REPO_PATTERN)
    base_branch: str | None = Field(default=None, max_length=200)
    logs: str = Field(default="", max_length=200_000)
    test_output: str = Field(default="", max_length=100_000)


class Evidence(BaseModel):
    id: str
    kind: Literal["log", "file", "commit", "test", "ci"]
    ref: str
    excerpt: str


class Hypothesis(BaseModel):
    description: str
    supporting_evidence: list[str] = []  # evidence ids
    assumptions: list[str] = []
    additional_evidence_needed: list[str] = []
    confidence: Literal["low", "medium", "high"] = "low"


class FileEdit(BaseModel):
    """Exact-match replacement. An empty `search` creates a new file."""

    path: str
    search: str = ""
    replace: str


class PatchProposal(BaseModel):
    attempt: int = 1
    summary: str
    rationale: str = ""
    edits: list[FileEdit]
    diff: str = ""
    tests_to_run: list[str] = []
    risk: Risk = "medium"
    sha256: str = ""
    changed_lines: int = 0


class PolicyDecision(BaseModel):
    allowed: bool
    requires_approval: bool
    risk: Risk
    reasons: list[str] = []


class ApprovalRecord(BaseModel):
    approver: str
    patch_sha256: str
    comment: str = ""
    approved_at: datetime = Field(default_factory=utcnow)


class VerificationResult(BaseModel):
    attempt: int
    passed: bool
    conclusion: str
    run_url: str | None = None
    failed_jobs: list[str] = []
    log_excerpt: str = ""


class Incident(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    status: IncidentStatus = IncidentStatus.RECEIVED
    request: IncidentCreate
    base_branch: str | None = None
    base_sha: str | None = None
    root_cause_summary: str = ""
    evidence: list[Evidence] = []
    hypotheses: list[Hypothesis] = []
    patch: PatchProposal | None = None
    policy: PolicyDecision | None = None
    approval: ApprovalRecord | None = None
    attempts: int = 0
    llm_calls: int = 0
    branch: str | None = None
    verifications: list[VerificationResult] = []
    pull_request_url: str | None = None
    notes: list[str] = []
    error: str | None = None
