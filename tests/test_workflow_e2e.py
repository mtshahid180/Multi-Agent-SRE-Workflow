import hashlib
import hmac
import json
from pathlib import Path

from tests.conftest import GOOD_EDIT, FakeLLM, FakeRepo


def submit(client, body):
    r = client.post("/incidents", json=body)
    assert r.status_code == 202, r.text
    return r.json()["id"]


def approve(client, iid, sha, approver="alice"):
    return client.post(
        f"/incidents/{iid}/approve", json={"approver": approver, "patch_sha256": sha}
    )


def test_health_is_public_but_incidents_need_key(make_env):
    client, _, _ = make_env()
    assert client.get("/health", headers={"X-API-Key": ""}).status_code == 200
    assert client.get("/incidents", headers={"X-API-Key": "wrong"}).status_code == 401


def test_repo_not_allowed(make_env, incident_body):
    client, _, _ = make_env()
    r = client.post("/incidents", json={**incident_body, "repository": "evil/repo"})
    assert r.status_code == 403


def test_happy_path_to_pull_request(make_env, incident_body):
    client, repo, _ = make_env()
    iid = submit(client, incident_body)
    inc = client.get(f"/incidents/{iid}").json()
    assert inc["status"] == "awaiting_approval"
    assert inc["hypotheses"][0]["supporting_evidence"] == ["E1"]  # invented id dropped
    assert any(e["kind"] == "file" and e["ref"] == "shop/stats.py" for e in inc["evidence"])
    assert "if values else 0" in inc["patch"]["diff"]
    assert repo.pushed == []  # nothing is pushed before approval

    assert approve(client, iid, "0" * 64).status_code == 409  # wrong hash
    sha = inc["patch"]["sha256"]
    assert approve(client, iid, sha).status_code == 202

    inc = client.get(f"/incidents/{iid}").json()
    assert inc["status"] == "pr_created"
    assert inc["pull_request_url"].endswith("/pull/7")
    branch, files = repo.pushed[0]
    assert branch == f"sre-agent/{iid}-a1" and "if values else 0" in files["shop/stats.py"]
    assert repo.pulls[0]["draft"] is True and repo.pulls[0]["base"] == "main"
    events = [e["type"] for e in client.get(f"/incidents/{iid}/events").json()]
    assert "approved" in events and "pull_request_created" in events


def test_cannot_approve_twice(make_env, incident_body):
    client, _, _ = make_env()
    iid = submit(client, incident_body)
    sha = client.get(f"/incidents/{iid}/patch").json()["patch"]["sha256"]
    assert approve(client, iid, sha).status_code == 202
    assert approve(client, iid, sha).status_code == 409


def test_reject(make_env, incident_body):
    client, repo, _ = make_env()
    iid = submit(client, incident_body)
    r = client.post(f"/incidents/{iid}/reject", json={"approver": "bob", "comment": "no"})
    assert r.json()["status"] == "rejected" and repo.pushed == []


def test_ci_failure_retries_then_succeeds_with_fresh_approval(make_env, incident_body):
    second = {**GOOD_EDIT, "summary": "second try"}
    client, repo, _ = make_env(
        llm=FakeLLM([GOOD_EDIT, second]), repo=FakeRepo(ci=["failure", "success"])
    )
    iid = submit(client, incident_body)
    sha1 = client.get(f"/incidents/{iid}/patch").json()["patch"]["sha256"]
    approve(client, iid, sha1)
    inc = client.get(f"/incidents/{iid}").json()
    # the retry produced a NEW patch which needs its own approval
    assert inc["status"] == "awaiting_approval" and inc["attempts"] == 2
    assert inc["verifications"][0]["passed"] is False
    assert "[REDACTED" in inc["verifications"][0]["log_excerpt"]
    approve(client, iid, inc["patch"]["sha256"])
    inc = client.get(f"/incidents/{iid}").json()
    assert inc["status"] == "pr_created"
    assert [b for b, _ in repo.pushed] == [f"sre-agent/{iid}-a1", f"sre-agent/{iid}-a2"]


def test_retries_are_bounded(make_env, incident_body):
    client, repo, _ = make_env(
        llm=FakeLLM([GOOD_EDIT, GOOD_EDIT]),
        repo=FakeRepo(ci=["failure", "failure"]),
        max_fix_attempts=2,
    )
    iid = submit(client, incident_body)
    for _ in range(2):
        inc = client.get(f"/incidents/{iid}").json()
        approve(client, iid, inc["patch"]["sha256"])
    inc = client.get(f"/incidents/{iid}").json()
    assert inc["status"] == "failed" and inc["pull_request_url"] is None
    assert repo.pulls == []


def test_policy_denies_workflow_edits(make_env, incident_body):
    evil = {
        **GOOD_EDIT,
        "edits": [{"path": ".github/workflows/x.yml", "search": "", "replace": "name: pwn"}],
    }
    client, repo, _ = make_env(llm=FakeLLM([evil]))
    iid = submit(client, incident_body)
    inc = client.get(f"/incidents/{iid}").json()
    assert inc["status"] == "denied" and repo.pushed == []
    sha = inc["patch"]["sha256"]
    assert approve(client, iid, sha).status_code == 409


def test_search_must_match_exactly_once_and_one_repair_round(make_env, incident_body):
    bad = {**GOOD_EDIT, "edits": [{"path": "shop/stats.py", "search": "nope", "replace": "x"}]}
    client, _, _ = make_env(llm=FakeLLM([bad, GOOD_EDIT]))
    iid = submit(client, incident_body)
    assert client.get(f"/incidents/{iid}").json()["status"] == "awaiting_approval"


def test_model_cannot_edit_files_it_was_not_shown(make_env, incident_body):
    bad = {**GOOD_EDIT, "edits": [{"path": "other.py", "search": "a", "replace": "b"}]}
    client, _, _ = make_env(llm=FakeLLM([bad, bad]), repo=FakeRepo({"shop/stats.py": "x = 1\n"}))
    iid = submit(client, incident_body)
    inc = client.get(f"/incidents/{iid}").json()
    assert inc["status"] == "analyzed" and inc["patch"] is None


def test_llm_failure_falls_back_to_heuristics(make_env, incident_body):
    client, repo, _ = make_env(llm=FakeLLM(fail=True))
    iid = submit(client, incident_body)
    inc = client.get(f"/incidents/{iid}").json()
    assert inc["status"] == "analyzed" and inc["patch"] is None
    assert "ZeroDivisionError" in inc["root_cause_summary"]
    assert repo.pushed == []


def test_llm_budget_is_enforced(make_env, incident_body):
    client, _, deps = make_env(max_llm_calls_per_incident=1)
    iid = submit(client, incident_body)
    inc = client.get(f"/incidents/{iid}").json()
    assert inc["llm_calls"] == 1 and inc["status"] == "analyzed"


def test_secrets_in_logs_are_redacted(make_env, incident_body):
    client, _, _ = make_env()
    body = {**incident_body, "logs": incident_body["logs"] + "token=ghp_" + "b" * 30}
    iid = submit(client, body)
    assert "ghp_" not in json.dumps(client.get(f"/incidents/{iid}").json())


def test_local_repo_flow_needs_no_github(make_env):
    local_fix = {
        "can_fix": True,
        "summary": "guard empty",
        "risk": "low",
        "edits": [
            {
                "path": "calculator/stats.py",
                "search": "    return total / len(values)",
                "replace": "    return total / len(values) if values else 0",
            }
        ],
    }
    client, repo, _ = make_env(llm=FakeLLM([local_fix]))
    body = json.loads(Path("examples/sample_app/incident.json").read_text())
    iid = submit(client, body)
    inc = client.get(f"/incidents/{iid}").json()
    assert inc["status"] == "awaiting_approval"
    approve(client, iid, inc["patch"]["sha256"])
    inc = client.get(f"/incidents/{iid}").json()
    assert inc["status"] == "approved" and any("apply the diff" in n for n in inc["notes"])
    assert repo.pushed == []


def test_auto_approve_low_risk(make_env, incident_body):
    client, repo, _ = make_env(require_approval=True, auto_approve_low_risk=True)
    iid = submit(client, incident_body)
    assert client.get(f"/incidents/{iid}").json()["status"] == "pr_created"
    assert repo.pushed


def _sign(body: bytes, secret="whsec"):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _hook(client, payload, delivery, event="issues", sig=None):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": sig or _sign(body),
            "Content-Type": "application/json",
            "X-API-Key": "",
        },
    )


def test_webhook_signature_replay_and_label(make_env):
    client, _, _ = make_env()
    from tests.conftest import TRACE

    payload = {
        "action": "labeled",
        "repository": {"full_name": "acme/shop"},
        "issue": {"title": "boom", "body": TRACE, "labels": [{"name": "incident"}]},
    }
    assert _hook(client, payload, "d1", sig="sha256=bad").status_code == 401
    r = _hook(client, payload, "d1")
    assert r.json()["status"] == "accepted"
    assert _hook(client, payload, "d1").json()["status"] == "duplicate"
    payload["issue"]["labels"] = [{"name": "bug"}]
    assert _hook(client, payload, "d2").json()["status"] == "ignored"
    assert _hook(client, {}, "d3", event="ping").json()["status"] == "pong"
