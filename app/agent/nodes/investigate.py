"""Collect evidence: error excerpts from logs, referenced source files and recent commits."""

import re

from app.agent.nodes.common import ACTOR, fail, set_status
from app.agent.state import AgentState, Deps
from app.integrations.github_client import GitHubError
from app.schemas.incident import Evidence, Incident, IncidentStatus
from app.security.redaction import redact

_PATTERNS = [
    re.compile(r'File "([^"]+)", line (\d+)'),  # Python traceback
    re.compile(r"^([\w./\\-]+\.py):(\d+): ", re.M),  # pytest summary
    re.compile(r"([\w./\\-]+\.(?:js|jsx|ts|tsx|mjs|cjs)):(\d+)(?::\d+)?"),  # Node / browsers
    re.compile(r"(?:^|\s)([\w./\\-]+\.go):(\d+)", re.M),  # Go
]
_NOISE = (
    "site-packages",
    "node_modules",
    "dist-packages",
    "/usr/lib/",
    "<frozen",
    "<string>",
    "<stdin>",
    "internal/",
)
_ERROR_LINE = re.compile(r"(?i)(error|exception|traceback|fatal|panic|assert|failed)")


def extract_locations(text: str) -> list[tuple[str, int]]:
    """Ordered, de-duplicated (path, line) pairs for project files mentioned in the text."""
    found: dict[str, int] = {}
    for pattern in _PATTERNS:
        for m in pattern.finditer(text):
            path = m.group(1).replace("\\", "/")
            if any(n in path for n in _NOISE) or path in found:
                continue
            found[path] = int(m.group(2))
    return list(found.items())


def error_excerpt(text: str, max_lines: int = 40) -> str:
    lines = text.strip().splitlines()
    if not lines:
        return ""
    idx = [i for i, ln in enumerate(lines) if _ERROR_LINE.search(ln)]
    if not idx:
        return "\n".join(lines[-max_lines:])
    start = max(0, idx[0] - 3)
    return "\n".join(lines[start : start + max_lines])[:4000]


def _candidates(path: str) -> list[str]:
    parts = [p for p in path.split("/") if p not in ("", ".")]
    return ["/".join(parts[i:]) for i in range(min(len(parts), 6))]


def _numbered_window(content: str, line: int, radius: int = 12) -> str:
    lines = content.splitlines()
    lo, hi = max(0, line - 1 - radius), min(len(lines), line + radius)
    return "\n".join(f"{n + 1:>4}: {lines[n]}" for n in range(lo, hi))[:3000]


def run(state: AgentState, deps: Deps) -> dict:
    incident_id = state["incident_id"]
    incident = set_status(deps, incident_id, IncidentStatus.INVESTIGATING)
    req = incident.request
    source = deps.repos.get(req.repository)
    evidence: list[Evidence] = []

    def add(kind: str, ref: str, excerpt: str) -> str:
        eid = f"E{len(evidence) + 1}"
        evidence.append(Evidence(id=eid, kind=kind, ref=ref, excerpt=redact(excerpt)))
        return eid

    try:
        branch = req.base_branch or source.default_branch(req.repository)
        base_sha = source.resolve_ref(req.repository, branch)

        if req.logs.strip():
            add("log", "incident logs", error_excerpt(req.logs))
        if req.test_output.strip():
            add("test", "test output", error_excerpt(req.test_output))

        locations = extract_locations(req.logs + "\n" + req.test_output)
        fetched = 0
        for raw_path, line in locations:
            if fetched >= deps.settings.max_context_files:
                break
            for candidate in _candidates(raw_path):
                content = source.get_file(req.repository, candidate, base_sha)
                if content is None:
                    continue
                if len(content) > deps.settings.max_file_chars:
                    break  # too large to send to the model; skip this file
                add("file", candidate, _numbered_window(content, line))
                fetched += 1
                for c in source.recent_commits(req.repository, candidate, base_sha, 3):
                    add("commit", candidate, f"{c['sha']} {c['date']} {c['message']}")
                break
    except GitHubError as exc:
        return fail(deps, incident_id, f"Investigation failed: {exc}")

    def mutate(inc: Incident) -> None:
        inc.base_branch, inc.base_sha, inc.evidence = branch, base_sha, evidence

    deps.store.update(incident_id, mutate)
    deps.store.add_event(
        incident_id,
        ACTOR,
        "evidence_collected",
        {"count": len(evidence), "files": [e.ref for e in evidence if e.kind == "file"]},
    )
    return {"outcome": "ok"}
