import json

import httpx
import pytest

from app.agent.nodes.investigate import error_excerpt, extract_locations
from app.agent.patching import PatchApplyError, apply_edits, make_diff
from app.config import Settings
from app.integrations.github_client import GitHubClient, LocalRepoSource
from app.integrations.llm_client import AnthropicLLM, LLMError, extract_json
from app.schemas.incident import FileEdit, PatchProposal
from app.security.policies import evaluate_patch
from app.security.redaction import redact

S = Settings(allowed_repos="acme/shop", github_token="t", llm_api_key="k")


def patch(paths, lines=2, risk="low"):
    return PatchProposal(
        summary="s",
        edits=[FileEdit(path=p, search="a", replace="b") for p in paths],
        changed_lines=lines,
        risk=risk,
    )


def test_apply_edits_rules():
    files = {"a.py": "x = 1\nx = 1\ny = 2\n"}
    assert apply_edits(files, [FileEdit(path="a.py", search="y = 2", replace="y = 3")])[
        "a.py"
    ].endswith("y = 3\n")
    with pytest.raises(PatchApplyError):
        apply_edits(files, [FileEdit(path="a.py", search="x = 1", replace="z")])  # ambiguous
    with pytest.raises(PatchApplyError):
        apply_edits(files, [FileEdit(path="a.py", search="zzz", replace="z")])  # missing
    with pytest.raises(PatchApplyError):
        apply_edits(files, [FileEdit(path="a.py", search="", replace="z")])  # exists
    new = apply_edits(files, [FileEdit(path="t.py", search="", replace="ok\n")])
    diff, n = make_diff({**files, "t.py": None}, new)
    assert "--- /dev/null" in diff and n == 1


def test_policy():
    assert evaluate_patch(patch(["app/x.py"]), S, "acme/shop").allowed
    for bad in [".github/workflows/ci.yml", ".env", "../etc/passwd", "/abs.py", "keys/server.pem"]:
        assert not evaluate_patch(patch([bad]), S, "acme/shop").allowed, bad
    d = evaluate_patch(patch(["db/migrations/001.py"]), S, "acme/shop")
    assert d.allowed and d.risk == "high" and d.requires_approval
    assert not evaluate_patch(patch(["a.py"], lines=500), S, "acme/shop").allowed
    assert not evaluate_patch(patch(["a.py"]), S, "other/repo").allowed
    assert not evaluate_patch(patch([f"f{i}.py" for i in range(9)]), S, "acme/shop").allowed
    auto = Settings(allowed_repos="acme/shop", auto_approve_low_risk=True)
    assert not evaluate_patch(patch(["a.py"]), auto, "acme/shop").requires_approval
    assert evaluate_patch(patch(["auth/login.py"]), auto, "acme/shop").requires_approval


def test_redaction():
    out = redact("password=hunter2 Bearer abcdefghijklmnopqrstuvwxyz ghp_" + "x" * 30 + " ok")
    assert "hunter2" not in out and "ghp_" not in out and "abcdefghijkl" not in out and "ok" in out


def test_trace_parsing():
    text = (
        'File "/usr/lib/python3/site-packages/x.py", line 1\n'
        'File "C:\\app\\shop\\stats.py", line 7, in f\n'
        "at handler (/srv/api/src/h.ts:10:5)\nValueError: bad"
    )
    assert extract_locations(text) == [("C:/app/shop/stats.py", 7), ("/srv/api/src/h.ts", 10)]
    assert "ValueError" in error_excerpt(text)


def test_extract_json():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(LLMError):
        extract_json("no json here")


def test_anthropic_client_request_and_errors():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["key"] = request.headers["x-api-key"]
        return httpx.Response(200, json={"content": [{"type": "text", "text": '{"ok": true}'}]})

    llm = AnthropicLLM(S, transport=httpx.MockTransport(handler))
    assert llm.complete_json("sys", "usr") == {"ok": True}
    assert seen["key"] == "k" and seen["body"]["system"] == "sys"
    bad = AnthropicLLM(S, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(LLMError):
        bad.complete_json("s", "u")


def test_github_client_calls():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        p = request.url.path
        if p.endswith("/contents/missing.py"):
            return httpx.Response(404, json={"message": "Not Found"})
        if p.endswith("/contents/a.py"):
            return httpx.Response(200, text="print(1)\n")
        if "/git/commits/" in p and request.method == "GET":
            return httpx.Response(200, json={"tree": {"sha": "T0"}})
        if p.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "T1"})
        if p.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "C1"})
        if p.endswith("/git/refs"):
            assert json.loads(request.content)["ref"] == "refs/heads/sre-agent/x"
            return httpx.Response(201, json={})
        if p.endswith("/pulls"):
            assert json.loads(request.content)["draft"] is True
            return httpx.Response(201, json={"html_url": "https://github.com/acme/shop/pull/1"})
        return httpx.Response(403, json={"message": "nope"})

    gh = GitHubClient(S, transport=httpx.MockTransport(handler))
    assert gh.get_file("acme/shop", "a.py", "main") == "print(1)\n"
    assert gh.get_file("acme/shop", "missing.py", "main") is None
    assert gh.push_branch("acme/shop", "sre-agent/x", "B" * 40, {"a.py": "x"}, "msg") == "C1"
    assert gh.create_pull_request("acme/shop", "t", "b", "sre-agent/x", "main", True).endswith("/1")
    with pytest.raises(Exception, match="403"):
        gh.default_branch("acme/shop")


def test_local_repo_blocks_path_traversal():
    src = LocalRepoSource("examples")
    assert "def average" in src.get_file("local:sample_app", "calculator/stats.py", "local")
    assert src.get_file("local:sample_app", "../../README.md", "local") is None
