"""Frontend tests: step states for every real backend scenario, and page rendering."""

import pytest

pytest.importorskip("flask")

import frontend.app as fe  # noqa: E402
from tests.conftest import GOOD_EDIT, TRACE, FakeLLM, FakeRepo  # noqa: E402


def states(inc):
    return {s["key"]: s["state"] for s in fe.build_steps(inc)}


@pytest.fixture
def env(make_env, monkeypatch):
    def _make(**kw):
        client, repo, deps = make_env(**kw)
        monkeypatch.setattr(fe, "HTTP", client)
        monkeypatch.setattr(fe, "_api_key", lambda: "k")
        return client, repo

    return _make


BODY = {"title": "Checkout 500s", "repository": "acme/shop", "logs": TRACE}


def submit(client, body=BODY):
    return client.post("/incidents", json=body).json()["id"]


def approve(client, iid):
    sha = client.get(f"/incidents/{iid}").json()["patch"]["sha256"]
    client.post(f"/incidents/{iid}/approve", json={"approver": "ali", "patch_sha256": sha})


def get(client, iid):
    return client.get(f"/incidents/{iid}").json()


def test_waiting_for_approval(env):
    client, _ = env()
    st = states(get(client, submit(client)))
    assert [st[k] for k in fe.KEYS[:5]] == ["done"] * 5
    assert st["approval"] == "waiting"
    assert [st[k] for k in ("branch", "ci", "pr")] == ["pending"] * 3


def test_full_success(env):
    client, _ = env()
    iid = submit(client)
    approve(client, iid)
    inc = get(client, iid)
    assert set(states(inc).values()) == {"done"}
    assert fe.headline(inc, fe.build_steps(inc))["tone"] == "green"


def test_no_patch_is_stopped_not_failed(env):
    client, _ = env(llm=FakeLLM(fail=True))
    st = states(get(client, submit(client)))
    assert st["root_cause"] == "done" and st["patch"] == "stopped"
    assert st["approval"] == "skipped" and st["pr"] == "skipped"


def test_policy_denied(env):
    evil = {
        **GOOD_EDIT,
        "edits": [{"path": ".github/workflows/x.yml", "search": "", "replace": "a"}],
    }
    client, _ = env(llm=FakeLLM([evil]))
    inc = get(client, submit(client))
    st = states(inc)
    assert st["policy"] == "failed" and st["approval"] == "skipped"
    assert (
        "protected path" in next(s for s in fe.build_steps(inc) if s["key"] == "policy")["detail"]
    )


def test_rejected(env):
    client, _ = env()
    iid = submit(client)
    client.post(f"/incidents/{iid}/reject", json={"approver": "bob"})
    st = states(get(client, iid))
    assert st["approval"] == "failed" and st["branch"] == "skipped"


def test_local_repo_approved_is_read_only(env):
    local = {
        "can_fix": True,
        "summary": "g",
        "risk": "low",
        "edits": [
            {
                "path": "calculator/stats.py",
                "search": "    return total / len(values)",
                "replace": "    return total / len(values) if values else 0",
            }
        ],
    }
    client, _ = env(llm=FakeLLM([local]))
    iid = submit(
        client,
        {
            "title": "t",
            "repository": "local:sample_app",
            "logs": 'File "/app/calculator/stats.py", line 6\nValueError: x',
        },
    )
    approve(client, iid)
    inc = get(client, iid)
    st = states(inc)
    assert st["approval"] == "done" and st["branch"] == "skipped" and st["pr"] == "skipped"
    assert fe.headline(inc, fe.build_steps(inc))["title"] == "Approved (local sample)"


def test_retry_after_ci_failure_then_exhausted(env):
    client, _ = env(llm=FakeLLM([GOOD_EDIT, GOOD_EDIT]), repo=FakeRepo(ci=["failure", "failure"]))
    iid = submit(client)
    approve(client, iid)
    st = states(get(client, iid))  # second patch written, needs approval again
    assert st["approval"] == "waiting" and st["branch"] == "pending" and st["ci"] == "pending"
    approve(client, iid)
    inc = get(client, iid)
    st = states(inc)
    assert inc["status"] == "failed" and st["ci"] == "failed" and st["pr"] == "skipped"
    assert fe.headline(inc, fe.build_steps(inc))["tone"] == "red"


def test_pages_render_and_actions_work(env):
    client, repo = env()
    web = fe.app.test_client()
    home = web.get("/")
    assert home.status_code == 200 and b"How it works" in home.data and b"Quick sample" in home.data

    xss = {**BODY, "logs": TRACE + "<script>alert(1)</script>"}
    iid = submit(client, xss)
    page = web.get(f"/incident/{iid}")
    html = page.data.decode()
    assert page.status_code == 200 and "Your turn" in html
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert 'class="diff"' in html and "if values else 0" in html
    assert "Audit trail" in html and "Approve" in html

    assert (
        web.post(f"/incident/{iid}/approve", data={"approver": "", "patch_sha256": "x"}).status_code
        == 302
    )  # missing name -> flash, no API call
    assert get(client, iid)["status"] == "awaiting_approval"
    sha = get(client, iid)["patch"]["sha256"]
    r = web.post(
        f"/incident/{iid}/approve",
        data={"approver": "ali", "patch_sha256": sha},
        follow_redirects=True,
    )
    assert b"Pull Request ready" in r.data and repo.pulls
    assert web.get(f"/incident/{iid}/state").json["status"] == "pr_created"

    r = web.post("/submit-sample")  # built-in local sample
    assert r.status_code == 302 and "/incident/" in r.headers["Location"]
    r = web.post("/submit", data={"title": "", "repository": ""}, follow_redirects=True)
    assert b"required" in r.data
    r = web.post("/submit", data={"title": "x", "repository": "evil/repo"}, follow_redirects=True)
    assert r.status_code == 502 and b"ALLOWED_REPOS" in r.data
    assert web.get("/incident/doesnotexist").status_code == 502


def test_api_offline_shows_friendly_page(monkeypatch):
    import httpx

    class Broken:
        def request(self, *a, **k):
            raise httpx.ConnectError("down")

    monkeypatch.setattr(fe, "HTTP", Broken())
    r = fe.app.test_client().get("/")
    assert r.status_code == 200 and b"API offline" in r.data
    r = fe.app.test_client().get("/incident/abc")
    assert r.status_code == 502 and b"uvicorn app.main:create_app" in r.data
