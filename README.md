<div align="center">

# 🤖 Multi Agent AI SRE Workflow

### *An Agentic AI system that investigates incidents, proposes patches, verifies them in CI — and delivers fixes through Pull Requests.*

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.156-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-Agent%20Orchestration-E01E5B?style=flat-square)](https://langchain-ai.github.io/langgraph/)
[![Tests](https://img.shields.io/badge/33-Tests%20Passing-brightgreen?style=flat-square)](#-testing)
[![Ruff](https://img.shields.io/badge/Lint-Ruff-F7B93E?style=flat-square&logo=ruff&logoColor=black)](https://docs.astral.sh/ruff/)
[![Docker-Free](https://img.shields.io/badge/%F0%9F%9A%A0%20Docker-Free-2496ED?style=flat-square&logo=docker&logoColor=white)](#-docker-free)
[![License](https://img.shields.io/badge/License-Not%20Yet%20Selected-lightgrey?style=flat-square)](#-license)

[**Overview**](#-overview) •
[**Workflow**](#-workflow) •
[**Architecture**](#️-architecture) •
[**Quick Start**](#-quick-start) •
[**API**](#-api-reference) •
[**Roadmap**](#-roadmap)

</div>

---

## 📌 Overview

The **AI SRE Agent** connects to an authorized GitHub repository, investigates an incident
using evidence (logs, stack traces, code, tests), forms a root-cause hypothesis, proposes a
**focused patch**, verifies it on a **remote GitHub Actions runner**, and opens a **draft Pull
Request** — with a mandatory **human approval gate** before anything is ever delivered.

> **Status:** Working MVP — core Phases 1–4 implemented, **33 automated tests**.
> **Safety first:** AI-generated code is never executed on your machine; verification happens
> remotely in a restricted CI workflow.

### ✨ Highlights

| | |
|---|---|
| 🔍 **Evidence-driven** | Every hypothesis cites concrete evidence — logs, files, commits, tests |
| 🧠 **LLM-optional** | Works offline with heuristic analysis; add an API key for AI patches |
| 🚧 **Human-in-the-loop** | Approval gate with SHA-256 patch binding — nothing ships unreviewed |
| 🏝️ **Local mode** | `local:` repos let you test everything with zero GitHub setup |
| 🔒 **Least privilege** | Allowlisted repos, secret redaction, bounded retries, no local code exec |
| 🐳 **Docker-free** | Plain `venv` + `pip` — no containers required |

---

## ⚙️ Workflow

### 🔁 Incident Lifecycle (State Machine)

```mermaid
stateDiagram-v2
    [*] --> RECEIVED: POST /incidents
    RECEIVED --> INVESTIGATING: agent starts
    INVESTIGATING --> ANALYZED: no patch possible
    INVESTIGATING --> AWAITING_APPROVAL: patch proposed
    AWAITING_APPROVAL --> APPROVED: POST /approve ✅
    AWAITING_APPROVAL --> REJECTED: POST /reject ❌
    AWAITING_APPROVAL --> DENIED: policy block 🚫
    APPROVED --> DELIVERING: push repair branch
    DELIVERING --> VERIFYING: CI triggered
    VERIFYING --> PR_CREATED: checks pass ✅
    VERIFYING --> INVESTIGATING: retry < MAX_FIX_ATTEMPTS
    VERIFYING --> FAILED: retries exhausted
    PR_CREATED --> [*]: human merges
    FAILED --> [*]
    REJECTED --> [*]
    DENIED --> [*]
```

### 🔀 Agent Orchestration Graph (LangGraph)

```mermaid
flowchart TD
    START([🎯 Incident Received]) --> ENTRY{Entry Router}
    ENTRY -->|status: received| INV
    ENTRY -->|status: approved| APPLY
    ENTRY -->|other| END1([⏹ END])

    subgraph PHASE1 [" Phase 1 — Analysis"]
        INV["🔎 investigate<br/><i>collect logs, files, commits, tests</i>"]
        RC["🧠 root_cause<br/><i>hypotheses with evidence</i>"]
        GEN["✏️ generate_patch<br/><i>minimal diff via LLM</i>"]
    end

    subgraph PHASE2 ["🛡 Phase 2 — Safety"]
        GATE{"policy_gate"}
    end

    subgraph PHASE3 [" Phase 3 — Delivery & Verification"]
        APPLY["📤 apply_patch<br/><i>push repair branch</i>"]
        VERIFY{"🧪 verify<br/><i>GitHub Actions CI</i>"}
        PR[" Pull Request<br/><i>draft + evidence</i>"]
    end

    INV -->|ok| RC --> GEN
    GEN -->|patch| GATE
    GEN -->|no_patch| END2([⏹ END — analyzed])

    GATE -->|auto-approved| APPLY
    GATE -->|await_approval| END3([⏸ PAUSED — human gate])
    GATE -->|denied| END4([🚫 END — denied])

    APPLY -->|pushed| VERIFY
    VERIFY -->|passed ✅| PR --> END5([✅ END — PR created])
    VERIFY -->|retry 🔄| GEN
    VERIFY -->|failed ❌| END6([❌ END — failed])
```

### 🌐 End-to-End Sequence

```mermaid
sequenceDiagram
    actor Dev as 👨‍💻 Developer
    participant API as 🌐 FastAPI API
    participant AG as 🤖 LangGraph Agent
    participant LLM as 🧠 LLM API
    participant GH as 🐙 GitHub
    participant RUN as ⚡ Actions Runner

    Dev->>API: POST /incidents (logs + repo)
    API->>AG: background task: run(graph)
    AG->>GH: fetch evidence (files, commits, tests)
    AG->>LLM: analyze → root cause + patch
    LLM-->>AG: hypotheses + diff
    AG->>API: status = awaiting_approval
    API-->>Dev: PATCH proposal (GET /patch)

    Dev->>API: POST /approve (sha256 ✅)
    API->>AG: resume → apply_patch
    AG->>GH: push repair branch
    AG->>RUN: trigger verify-patch.yml
    RUN->>RUN: ruff + pytest (remote!)
    RUN-->>AG: conclusion: success
    AG->>GH: open draft PR + evidence
    GH-->>Dev: 🔗 Pull Request URL
```

---

## 🏗️ Architecture

```mermaid
flowchart LR
    subgraph CLIENTS ["Clients"]
        UI["🖥️ Web UI<br/>Flask :5050"]
        CLI["⚡ curl / API client"]
        WH["🔔 GitHub Webhooks"]
    end

    subgraph CORE ["Core"]
        API["⚡ FastAPI Backend<br/>auth · validation · redaction"]
        GRAPH["🤖 LangGraph Orchestrator"]
        POLICY["🛡️ Policy & Approval Gate"]
        STORE[("💾 SQLite<br/>incidents.db")]
    end

    subgraph EXT ["External Services"]
        LLM["🧠 LLM API<br/>Anthropic / OpenAI"]
        GH["🐙 GitHub<br/>branches · PRs"]
        CI["⚡ GitHub Actions<br/>ruff + pytest"]
    end

    UI & CLI & WH --> API
    API --> GRAPH
    GRAPH --> STORE
    GRAPH <--> LLM
    GRAPH --> POLICY
    POLICY -- "approved" --> GH
    GH -- "workflow_dispatch" --> CI
    CI -- "results" --> GRAPH
```

### 📂 Repository Structure

```text
ai-sre-agent/
├── 📁 app/                        # Backend application
│   ├── main.py                    # FastAPI entrypoint (create_app)
│   ├── config.py                  # Pydantic settings (.env driven)
│   ├── 📁 agent/                  # LangGraph orchestration
│   │   ├── graph.py               #   state machine wiring
│   │   ├── runner.py              #   crash-safe graph executor
│   │   ├── prompts.py / patching.py / state.py
│   │   └── 📁 nodes/              #   investigate · root_cause ·
│   │                              #   generate_patch · policy_gate ·
│   │                              #   apply_patch · verify · open_pr
│   ├── 📁 api/                    # REST routes
│   │   ├── routes_incidents.py    #   incidents CRUD + patch view
│   │   ├── routes_approvals.py    #   approve / reject (human gate)
│   │   └── webhooks.py            #   validated GitHub events
│   ├── 📁 integrations/           # External services
│   │   ├── github_client.py       #   GitHub App / token + local repos
│   │   ├── llm_client.py          #   Anthropic / OpenAI (auto-detect)
│   │   └── test_runner.py         #   remote CI trigger + polling
│   ├── 📁 security/               # Guardrails
│   │   ├── policies.py            #   repo allowlist · risk limits
│   │   ├── approvals.py           #   SHA-256 bound approvals
│   │   └── redaction.py           #   secret scrubbing from logs
│   ├── 📁 schemas/incident.py     # Typed Pydantic models
│   └── 📁 storage/incidents.py    # SQLite persistence + audit events
├── 📁 frontend/                   # Flask web UI (:5050)
├── 📁 tests/                      # 33 tests — units · e2e · frontend
├── 📁 examples/sample_app/        # Offline demo repo + incident.json
├── 📁 .github/workflows/          # verify-patch.yml (CI template)
├── 📄 .env.example                # Configuration template
├── 📄 requirements.txt            # Pinned runtime deps
└── 📄 requirements-dev.txt        # pytest · ruff · flask
```

---

## 🛠️ Technology Stack

| Layer | Technology | Purpose |
|:--|:--|:--|
| 🐍 Language | **Python 3.12+** | Backend & agent implementation |
| 🤖 Orchestration | **LangGraph** | Stateful graph, checkpointing, pause/resume |
| ⚡ API | **FastAPI + Uvicorn** | Async endpoints, background agent runs |
| 🔍 Validation | **Pydantic v2** | Typed schemas & settings |
| 🧠 Model | **Anthropic / OpenAI** (auto-detect) | Root-cause reasoning & patch generation |
| 🐙 Repo Access | **GitHub App / fine-grained token** | Branches, diffs, PRs, webhooks |
| ⚡ CI | **GitHub Actions** | Remote, sandboxed patch verification |
| 💾 Storage | **SQLite** | Incidents, events, audit trail |
| 🖥️ Frontend | **Flask** | macOS-style review dashboard |
| 🧪 Testing | **Pytest** | Unit + workflow e2e + frontend tests |
| ✨ Linting | **Ruff** | Fast Python linter & formatter |

---

## 🚀 Quick Start

### Prerequisites

- Python **3.12+** · Git · (optional) GitHub account & LLM API key
- **No Docker required** 🎉

### 1 · Setup

```powershell
# Clone & enter
git clone https://github.com/<your-user>/ai-sre-agent.git
cd ai-sre-agent

# Virtual environment
py -m venv .venv
.\.venv\Scripts\Activate.ps1        # PowerShell
# .venv\Scripts\activate.bat        # CMD (if execution policy blocks you)

# Install dependencies
pip install -r requirements-dev.txt

# Configure
copy .env.example .env              # then edit with your keys
```

### 2 · Test

```powershell
python -m pytest -q          # ✅ 33 tests — no network needed
ruff check .                 # ✅ lint clean
```

### 3 · Run

```powershell
# Terminal 1 — API (http://127.0.0.1:8000)
uvicorn app.main:create_app --factory --reload

# Terminal 2 — Web UI (http://127.0.0.1:5050)
python frontend/app.py
```

### 4 · Try it offline (no GitHub, no API key) 🏝️

With `ALLOWED_REPOS=local:sample_app` in `.env`:

```powershell
curl -X POST http://127.0.0.1:8000/incidents `
  -H "content-type: application/json" `
  -d "@examples/sample_app/incident.json"

curl http://127.0.0.1:8000/incidents/<id>
```

| Configuration | What you get |
|:--|:--|
| No `LLM_API_KEY` | Evidence collection + **heuristic** root cause |
| With `LLM_API_KEY` | + AI root cause & proposed diff at `GET /incidents/<id>/patch` |
| `local:` repos | Read-only — approved patches shown for manual `git apply` |

### 5 · Real GitHub flow 🐙

1. Create a **GitHub App** (or a fine-grained token for *one* test repo) with:
   `Contents: write` · `Pull requests: write` · `Actions: write` · `Metadata: read`
2. Copy `.github/workflows/verify-patch.yml` to the **default branch** of the target repo
3. Set `ALLOWED_REPOS=owner/repo` in `.env`
4. Flow: submit incident → `awaiting_approval` → `POST /incidents/<id>/approve`
   with the patch's `sha256` → repair branch → remote CI → **draft Pull Request**
   (a failed CI run triggers a new patch needing fresh approval, up to `MAX_FIX_ATTEMPTS`)

---

## 📡 API Reference

<details>
<summary><b>Click to expand all endpoints</b></summary>

| Method | Endpoint | Description |
|:--|:--|:--|
| `POST` | `/incidents` | Submit an incident → agent starts in background **(202)** |
| `GET` | `/incidents` | List recent incidents |
| `GET` | `/incidents/{id}` | Full incident record (status, evidence, hypotheses) |
| `GET` | `/incidents/{id}/patch` | Proposed patch — diff, rationale, `sha256`, risk |
| `GET` | `/incidents/{id}/events` | Audit trail of every agent action |
| `POST` | `/incidents/{id}/approve` | ✅ Approve a patch — **requires its exact `sha256`** |
| `POST` | `/incidents/{id}/reject` | ❌ Reject a patch with a comment |
| `POST` | `/webhooks/github` | Validated GitHub webhook receiver |
| `GET` | `/health` | Liveness probe |

</details>

<details>
<summary><b>Example: submit → approve → PR</b></summary>

```bash
# 1️⃣ Submit
curl -X POST localhost:8000/incidents -H "content-type: application/json" \
  -d '{"title":"stats bug","repository":"local:sample_app","logs":"..."}'
# → {"id":"abc123","status":"received"}

# 2️⃣ Watch it pause at the gate
curl localhost:8000/incidents/abc123
# → status: awaiting_approval, patch.sha256: "9f86d0..."

# 3️⃣ Approve (the sha256 binds approval to THIS exact patch)
curl -X POST localhost:8000/incidents/abc123/approve \
  -H "content-type: application/json" \
  -d '{"approver":"me","patch_sha256":"9f86d08219..." }'

# 4️⃣ Collect the PR
curl localhost:8000/incidents/abc123
# → pull_request_url: "https://github.com/.../pull/1"
```

</details>

---

## 🛡️ Security & Guardrails

```mermaid
flowchart TD
    INPUT["📥 Untrusted input<br/>logs · issues · model output"] --> REDACT[" <br/>redact secrets"]
    REDACT --> ALLOW{"Repo in<br/>ALLOWED_REPOS?"}
    ALLOW -- no --> BLOCK403["🚫 403 Forbidden"]
    ALLOW -- yes --> INVESTIGATE
    INVESTIGATE["🔎 Investigate"] --> GATE{" Policy Gate "}
    GATE -- "risk > limits" --> DENY["🚫 DENIED"]
    GATE -- "needs human" --> HUMAN["⏸️ Awaiting Approval<br/>sha256-bound"]
    HUMAN -- "approve" --> REMOTE["☁️ Remote CI only<br/>never local execution"]
    HUMAN -- "reject" --> STOP["⏹ REJECTED"]
    REMOTE --> PR["🔒 Draft PR<br/>default branch untouched"]
```

-   **Least privilege** — GitHub App scoped to selected repositories only
-   **No local code execution** — AI-generated patches never run on your machine
-   **Remote verification** — restricted, predefined GitHub Actions workflow (read-only token, no secrets, 10-min timeout)
-   **Secret redaction** — logs/test output scrubbed before storage
-   **SHA-256 approval binding** — approving one patch cannot approve a swapped-in patch
-   **Repo allowlist** — only `ALLOWED_REPOS` can ever be touched
-   **Bounded retries** — `MAX_FIX_ATTEMPTS`, `MAX_LLM_CALLS_PER_INCIDENT`, CI timeouts
-   **Audit trail** — every action recorded in `/incidents/{id}/events`
-   **Default branch is sacred** — agent only pushes to dedicated repair branches

---

## 🧪 Testing

```powershell
python -m pytest -q        # 33 tests: units · workflow e2e · frontend
```

| Suite | Covers |
|:--|:--|
| `test_units.py` | Schemas, policies, redaction, approvals, storage |
| `test_workflow_e2e.py` | Full graph runs: analyze → gate → patch → verify |
| `test_frontend.py` | Flask UI routes & API proxying |

---

## 🐳 Docker-Free Development

Docker is **intentionally excluded**. Local isolation uses Python's built-in `venv`;
remote testing uses GitHub Actions hosted runners. This keeps the project runnable on
any machine with just Python + Git.

> ⚠️ `venv` is a dependency isolation tool, **not** a security sandbox. Never treat it as one.

---

## 🗺️ Roadmap

| Phase | Focus | Status |
|:--|:--|:--:|
| **1** | Working local MVP — API, schemas, LangGraph, patch proposals | ✅ |
| **2** | GitHub integration — repair branches, diffs, PRs | ✅ |
| **3** | Remote verification — Actions workflow, bounded retries | ✅ |
| **4** | Review interface & persistence — Flask UI, audit log | ✅ |
| **5** | Monitoring & controlled remediation — OTel, health checks, rollback | 🔜 |

### 🔜 Future Improvements

-   Incident deduplication & severity classification
-   Additional CI providers and repo platforms
-   Prometheus metrics & OpenTelemetry tracing
-   Role-based access control & richer approval policies
-   Deployment health checks and tested rollback workflows
-   Benchmarked repair success rate / cost-per-incident metrics

---

## ⚠️ Limitations

-   Root-cause analysis and patches **can be wrong** — human review remains mandatory
-   Passing tests don't prove production safety
-   The agent only sees repositories it's authorized to access
-   Hosted LLM APIs and CI runners require internet & may incur costs
-   Deployment / self-healing automation is future work (Phase 5)

---

## 🤝 Contributing

1.  Open an issue describing the change
2.  Add or update tests
3.  Run `ruff check .` and `python -m pytest -q`
4.  Document security-sensitive changes
5.  Submit a Pull Request

> 🚫 Never commit real secrets, sensitive logs, or credentials in issues, commits,
> test fixtures, or PRs.

---

## 📄 License

No license selected yet — add a `LICENSE` file before distributing or accepting
external contributions.

---

<div align="center">

**Built with  FastAPI · LangGraph · GitHub Actions · Pytest · Ruff**

*If this project helped you, consider giving it a ⭐ on GitHub!*

</div>
