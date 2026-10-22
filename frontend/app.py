"""Flask frontend (macOS-style UI) for the AI SRE Agent.

Run (the API must already be running):   python frontend/app.py
Then open:                                http://127.0.0.1:5050
"""

import json
import os
from datetime import datetime
from pathlib import Path

import httpx
from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

ROOT = Path(__file__).resolve().parent.parent
API = os.environ.get("SRE_API_URL", "http://127.0.0.1:8000")
SAMPLE_PATH = ROOT / "examples" / "sample_app" / "incident.json"

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", os.urandom(16))
HTTP = httpx.Client(base_url=API, timeout=30)


# ----------------------------------------------------------------- API access
class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _api_key() -> str:
    if os.environ.get("API_KEY"):
        return os.environ["API_KEY"]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def api(method: str, path: str, **kw):
    try:
        r = HTTP.request(method, path, headers={"X-API-Key": _api_key()}, **kw)
    except httpx.HTTPError:
        raise ApiError(0, f"Cannot reach the API at {API}. Is uvicorn running?") from None
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        if isinstance(detail, list):
            detail = "; ".join(str(d.get("msg", d)) for d in detail)
        raise ApiError(r.status_code, str(detail))
    return r.json()


# ------------------------------------------------------------- workflow model
STEP_DEFS = [
    (
        "received",
        "Incident received",
        "Saves your report, hides any passwords or tokens found in the logs, and checks that the "
        "repository is on the allowed list.",
    ),
    (
        "investigate",
        "Collect evidence",
        "Finds the files named in the error, reads them from the repository and looks at their "
        "recent commits.",
    ),
    (
        "root_cause",
        "Find the root cause",
        "Asks the AI why it failed. Every theory must point to evidence and list its assumptions.",
    ),
    (
        "patch",
        "Write a fix",
        "Asks the AI for the smallest possible change, then checks in memory that it applies "
        "cleanly. Nothing is executed.",
    ),
    (
        "policy",
        "Safety check",
        "Blocks risky changes (CI workflows, secrets, huge edits) and rates the risk low, medium "
        "or high.",
    ),
    (
        "approval",
        "Your approval",
        "The agent stops here and waits for you. Nothing touches your repository before this.",
    ),
    (
        "branch",
        "Repair branch",
        "Creates a separate branch containing the fix. Your main branch is never modified.",
    ),
    (
        "ci",
        "Run tests on GitHub",
        "Starts the repository's own test workflow on that branch and waits for the result.",
    ),
    (
        "pr",
        "Open Pull Request",
        "Opens a draft Pull Request with the evidence and test results for a human to review.",
    ),
]
KEYS = [d[0] for d in STEP_DEFS]
WORKING = {"received", "investigating", "delivering", "verifying"}
TERMINAL = {"analyzed", "denied", "rejected", "failed", "pr_created"}

STATUS_LABEL = {
    "received": "Received",
    "investigating": "Investigating",
    "analyzed": "Analysis only",
    "awaiting_approval": "Needs your approval",
    "approved": "Approved",
    "delivering": "Creating branch",
    "verifying": "Running tests",
    "pr_created": "Pull Request ready",
    "denied": "Blocked by policy",
    "rejected": "Rejected",
    "failed": "Failed",
}
STATUS_TONE = {
    "received": "blue",
    "investigating": "blue",
    "delivering": "blue",
    "verifying": "blue",
    "analyzed": "orange",
    "awaiting_approval": "orange",
    "approved": "green",
    "pr_created": "green",
    "denied": "red",
    "rejected": "gray",
    "failed": "red",
}
STATE_LABEL = {
    "done": "Done",
    "active": "Working",
    "waiting": "Waiting for you",
    "failed": "Failed",
    "stopped": "Stopped",
    "skipped": "Skipped",
    "pending": "Not started",
}
EVENT_LABEL = {
    "incident_received": "Incident received",
    "evidence_collected": "Evidence collected",
    "hypotheses_generated": "Root-cause theories written",
    "patch_proposed": "Fix proposed",
    "policy_decision": "Safety check finished",
    "approved": "Patch approved",
    "rejected": "Patch rejected",
    "branch_created": "Repair branch created",
    "verification_finished": "Tests finished",
    "pull_request_created": "Pull Request opened",
    "failed": "Run failed",
}


def _open_state(status: str, key: str, local_only: bool) -> str:
    if status in WORKING:
        return "active"
    if status == "awaiting_approval":
        return "waiting" if key == "approval" else "active"
    if status == "approved":
        return "skipped" if local_only else "active"
    if status == "analyzed":
        return "stopped"
    if status in ("denied", "rejected", "failed"):
        return "failed"
    return "active"


def _result(key: str, inc: dict) -> str:
    ev = inc.get("evidence") or []
    patch, policy, approval = inc.get("patch"), inc.get("policy"), inc.get("approval")
    vers = inc.get("verifications") or []
    if key == "received":
        return f"Repository: {inc['request']['repository']}"
    if key == "investigate":
        files = sum(1 for e in ev if e["kind"] == "file")
        commits = sum(1 for e in ev if e["kind"] == "commit")
        return f"{len(ev)} evidence items: {files} file(s), {commits} commit(s), logs"
    if key == "root_cause":
        how = "heuristic (no AI)" if inc["root_cause_summary"].startswith("Heuristic") else "AI"
        return f"[{how}] {inc['root_cause_summary'][:220]}"
    if key == "patch" and patch:
        n = len({e["path"] for e in patch["edits"]})
        return (
            f"{patch['summary']} ({n} file(s), {patch['changed_lines']} lines changed, "
            f"attempt {patch['attempt']})"
        )
    if key == "policy" and policy:
        need = "approval required" if policy["requires_approval"] else "auto-approved"
        return f"{'Allowed' if policy['allowed'] else 'Blocked'}, risk {policy['risk']}, {need}"
    if key == "approval" and approval:
        return f"Approved by {approval['approver']}"
    if key == "branch" and inc.get("branch"):
        return f"Branch {inc['branch']}"
    if key == "ci" and vers:
        v = vers[-1]
        return f"Tests {v['conclusion']} (attempt {v['attempt']})"
    if key == "pr" and inc.get("pull_request_url"):
        return inc["pull_request_url"]
    return ""


def _problem(inc: dict) -> str:
    st, notes = inc["status"], inc.get("notes") or []
    if st == "failed":
        return inc.get("error") or "The run failed."
    if st == "denied":
        return " ".join((inc.get("policy") or {}).get("reasons", [])) or "Blocked by policy."
    if st in ("analyzed", "rejected"):
        return notes[-1] if notes else ""
    return ""


def build_steps(inc: dict) -> list[dict]:
    st = inc["status"]
    patch, policy = inc.get("patch"), inc.get("policy")
    vers = inc.get("verifications") or []
    last_v = vers[-1] if vers else None
    local_only = st == "approved" and any("Local repositories" in n for n in inc["notes"])
    retrying = bool(
        patch
        and last_v
        and not last_v["passed"]
        and patch["attempt"] == last_v["attempt"]
        and st == "investigating"
    )
    patch_ok = patch is not None and st != "analyzed" and not retrying
    branch = inc.get("branch") or ""
    done = {
        "received": True,
        "investigate": bool(inc.get("base_sha")),
        "root_cause": bool(inc["hypotheses"]),
        "patch": patch_ok,
        "policy": patch_ok and policy is not None and policy["allowed"],
        "approval": patch_ok and inc.get("approval") is not None,
        "branch": patch_ok and branch.endswith(f"-a{patch['attempt']}"),
        "ci": bool(
            patch_ok and last_v and last_v["passed"] and last_v["attempt"] == patch["attempt"]
        ),
        "pr": bool(inc.get("pull_request_url")),
    }
    flags, ok = [], True
    for k in KEYS:  # a step only counts as done if every earlier step is done
        ok = ok and done[k]
        flags.append(ok)
    first_open = next((i for i, f in enumerate(flags) if not f), None)

    steps = []
    for i, (key, title, does) in enumerate(STEP_DEFS):
        if first_open is None or i < first_open:
            state = "done"
        elif i == first_open:
            state = _open_state(st, key, local_only)
        else:
            state = "skipped" if (st in TERMINAL or local_only) else "pending"
        detail = ""
        if state in ("failed", "stopped"):
            detail = _problem(inc)
        elif state == "skipped" and local_only and key == "branch":
            detail = "Local sample repositories are read-only. Copy the diff and apply it yourself."
        result = _result(key, inc) if state in ("done", "active", "waiting") else ""
        if key == "patch" and retrying:
            detail = "The tests failed, so the agent is writing a corrected fix."
        steps.append(
            {
                "n": i + 1,
                "key": key,
                "title": title,
                "does": does,
                "state": state,
                "state_label": STATE_LABEL[state],
                "result": result,
                "detail": detail,
            }
        )
    return steps


def headline(inc: dict, steps: list[dict]) -> dict:
    st = inc["status"]
    active = next((s for s in steps if s["state"] in ("active", "waiting")), None)
    key = active["key"] if active else ""
    if st == "pr_created":
        return {
            "tone": "green",
            "title": "Done",
            "text": "A draft Pull Request is ready. A human should review it before merging.",
        }
    if st == "awaiting_approval":
        return {
            "tone": "orange",
            "title": "Your turn",
            "text": "Read the proposed change below, then Approve or Reject. "
            "Nothing has been changed in your repository yet.",
        }
    if st == "approved":
        if any("Local repositories" in n for n in inc["notes"]):
            return {
                "tone": "green",
                "title": "Approved (local sample)",
                "text": "Nothing was pushed because local samples are read-only. "
                "Copy the diff and apply it yourself.",
            }
        return {"tone": "blue", "title": "Approved", "text": "Creating a safe repair branch..."}
    if st == "delivering":
        return {
            "tone": "blue",
            "title": "Creating the repair branch",
            "text": f"Pushing the fix to {inc.get('branch') or 'a new branch'}. "
            "Your main branch is untouched.",
        }
    if st == "verifying":
        return {
            "tone": "blue",
            "title": "Running tests on GitHub",
            "text": "The repository's own test workflow is running. This can take a few minutes.",
        }
    if st == "analyzed":
        return {
            "tone": "orange",
            "title": "Analysis finished, no patch",
            "text": _problem(inc) or "The agent could not propose a safe fix.",
        }
    if st == "denied":
        return {"tone": "red", "title": "Blocked by the safety policy", "text": _problem(inc)}
    if st == "rejected":
        return {
            "tone": "gray",
            "title": "You rejected this patch",
            "text": "Nothing was changed in your repository.",
        }
    if st == "failed":
        return {"tone": "red", "title": "Something went wrong", "text": _problem(inc)}
    text = {
        "investigate": "Reading your logs and fetching the files named in the error.",
        "root_cause": "Asking the AI to explain why it failed.",
        "patch": "Asking the AI to write the smallest possible fix and checking that it applies.",
    }.get(key, "Starting up...")
    return {"tone": "blue", "title": "Working on it", "text": text}


def diff_lines(diff: str):
    for line in (diff or "").splitlines():
        if line.startswith(("+++", "---")):
            cls = "hdr"
        elif line.startswith("@@"):
            cls = "hunk"
        elif line.startswith("+"):
            cls = "add"
        elif line.startswith("-"):
            cls = "del"
        else:
            cls = "ctx"
        yield cls, line


app.jinja_env.filters["diff_lines"] = diff_lines


# -------------------------------------------------------------------- routes
@app.context_processor
def inject_globals():
    try:
        recent, api_ok = api("GET", "/incidents?limit=15"), True
    except ApiError:
        recent, api_ok = [], False
    return {
        "recent": recent,
        "api_ok": api_ok,
        "label": lambda s: STATUS_LABEL.get(s, s),
        "tone": lambda s: STATUS_TONE.get(s, "gray"),
        "current_id": (request.view_args or {}).get("incident_id"),
    }


@app.errorhandler(ApiError)
def handle_api_error(err: ApiError):
    return render_template("error.html", message=err.message, status=err.status), 502


@app.get("/")
def home():
    return render_template("home.html", steps=STEP_DEFS)


def _submit(payload: dict):
    result = api("POST", "/incidents", json=payload)
    flash("Incident submitted. Watch the workflow below.", "ok")
    return redirect(url_for("incident", incident_id=result["id"]))


@app.post("/submit-sample")
def submit_sample():
    return _submit(json.loads(SAMPLE_PATH.read_text(encoding="utf-8")))


@app.post("/submit")
def submit_custom():
    f = request.form
    title, repo = f.get("title", "").strip(), f.get("repository", "").strip()
    if not title or not repo:
        flash("Title and repository are required.", "error")
        return redirect(url_for("home"))
    return _submit(
        {
            "title": title,
            "repository": repo,
            "base_branch": f.get("base_branch", "").strip() or None,
            "description": f.get("description", ""),
            "logs": f.get("logs", ""),
            "test_output": f.get("test_output", ""),
        }
    )


@app.get("/incident/<incident_id>")
def incident(incident_id: str):
    inc = api("GET", f"/incidents/{incident_id}")
    steps = build_steps(inc)
    events = api("GET", f"/incidents/{incident_id}/events")
    local_only = any("Local repositories" in n for n in inc["notes"])
    polling = inc["status"] in WORKING or (inc["status"] == "approved" and not local_only)
    return render_template(
        "incident.html",
        inc=inc,
        steps=steps,
        head=headline(inc, steps),
        events=events,
        event_label=EVENT_LABEL,
        polling=polling,
    )


@app.get("/incident/<incident_id>/state")
def incident_state(incident_id: str):
    inc = api("GET", f"/incidents/{incident_id}")
    return jsonify({"status": inc["status"], "updated_at": inc["updated_at"]})


@app.post("/incident/<incident_id>/approve")
def approve(incident_id: str):
    who = request.form.get("approver", "").strip()
    if not who:
        flash("Please type your name before approving.", "error")
        return redirect(url_for("incident", incident_id=incident_id))
    api(
        "POST",
        f"/incidents/{incident_id}/approve",
        json={
            "approver": who,
            "patch_sha256": request.form.get("patch_sha256", ""),
            "comment": request.form.get("comment", ""),
        },
    )
    flash("Approved. The agent is continuing.", "ok")
    return redirect(url_for("incident", incident_id=incident_id))


@app.post("/incident/<incident_id>/reject")
def reject(incident_id: str):
    who = request.form.get("approver", "").strip()
    if not who:
        flash("Please type your name before rejecting.", "error")
        return redirect(url_for("incident", incident_id=incident_id))
    api(
        "POST",
        f"/incidents/{incident_id}/reject",
        json={"approver": who, "comment": request.form.get("comment", "")},
    )
    flash("Patch rejected. Nothing was changed.", "ok")
    return redirect(url_for("incident", incident_id=incident_id))


def fmt_iso(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value


app.jinja_env.filters["fmt_iso"] = fmt_iso

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=True)