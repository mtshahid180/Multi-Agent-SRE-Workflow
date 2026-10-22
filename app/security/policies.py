"""Policy engine: decides whether a patch is allowed and whether it needs a human."""

import fnmatch
import posixpath

from app.config import Settings
from app.schemas.incident import RISK_ORDER, PatchProposal, PolicyDecision, Risk

# Paths the agent may never modify (CI definitions, secrets, key material).
DENY_PATTERNS = [
    ".github/workflows/*",
    ".github/actions/*",
    ".github/codeowners",
    "codeowners",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "*secret*",
    "*credential*",
]
# Paths that always force a human decision and a "high" risk rating.
HIGH_RISK_PATTERNS = [
    "*migration*",
    "*.sql",
    "*auth*",
    "*permission*",
    "*security*",
    "*iam*",
    "requirements*.txt",
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    "*.lock",
    "dockerfile*",
    "docker-compose*",
    "*.tf",
]


def _norm(path: str) -> str:
    # normpath already drops a leading "./"; never strip individual "." characters
    # (that would turn ".github" into "github" and bypass the deny list).
    return posixpath.normpath(path.replace("\\", "/")) if path else path


def _unsafe_path(path: str) -> bool:
    p = path.replace("\\", "/")
    return (
        not p or p.startswith("/") or ".." in p.split("/") or ":" in p.split("/")[0] or "\x00" in p
    )


def _matches(path: str, patterns: list[str]) -> bool:
    low = _norm(path).lower()
    base = posixpath.basename(low)
    return any(fnmatch.fnmatch(low, pat) or fnmatch.fnmatch(base, pat) for pat in patterns)


def is_repository_allowed(repository: str, settings: Settings) -> bool:
    return repository in settings.allowed_repo_list


def evaluate_patch(patch: PatchProposal, settings: Settings, repository: str) -> PolicyDecision:
    reasons: list[str] = []
    allowed = True
    risk: Risk = patch.risk

    if not is_repository_allowed(repository, settings):
        allowed = False
        reasons.append(f"Repository '{repository}' is not in ALLOWED_REPOS.")

    paths = {e.path for e in patch.edits}
    if not paths:
        allowed = False
        reasons.append("Patch contains no edits.")
    if len(paths) > settings.max_patch_files:
        allowed = False
        reasons.append(f"Patch touches {len(paths)} files (limit {settings.max_patch_files}).")
    if patch.changed_lines > settings.max_patch_lines:
        allowed = False
        reasons.append(
            f"Patch changes {patch.changed_lines} lines (limit {settings.max_patch_lines})."
        )

    for path in sorted(paths):
        if _unsafe_path(path):
            allowed = False
            reasons.append(f"Unsafe path rejected: {path!r}.")
        elif _matches(path, DENY_PATTERNS):
            allowed = False
            reasons.append(f"Modifying protected path is not permitted: {path}.")
        elif _matches(path, HIGH_RISK_PATTERNS):
            risk = "high"
            reasons.append(f"High-risk path requires human review: {path}.")

    if patch.changed_lines > settings.max_patch_lines // 2 and RISK_ORDER[risk] < 1:
        risk = "medium"
        reasons.append("Large patch; risk raised to medium.")

    if risk == "high":
        requires_approval = True
    elif settings.require_approval:
        requires_approval = not (settings.auto_approve_low_risk and risk == "low")
    else:
        requires_approval = False
    if requires_approval:
        reasons.append("Human approval required before any branch is created.")
    return PolicyDecision(
        allowed=allowed, requires_approval=requires_approval, risk=risk, reasons=reasons
    )
