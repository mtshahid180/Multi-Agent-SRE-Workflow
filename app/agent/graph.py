"""LangGraph workflow: investigate -> root cause -> patch -> policy gate -> deliver -> verify -> PR.

Incident data is persisted in the store, so the graph can stop at the approval gate and be
re-entered after a human approves (status APPROVED routes straight to apply_patch).
"""

from functools import partial

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    apply_patch,
    generate_patch,
    investigate,
    open_pr,
    policy_gate,
    root_cause,
    verify,
)
from app.agent.state import AgentState, Deps
from app.schemas.incident import IncidentStatus


def _outcome(state: AgentState) -> str:
    return state.get("outcome", "stop")


def build_graph(deps: Deps):
    g = StateGraph(AgentState)
    for name, module in [
        ("investigate", investigate),
        ("root_cause", root_cause),
        ("generate_patch", generate_patch),
        ("policy_gate", policy_gate),
        ("apply_patch", apply_patch),
        ("verify", verify),
        ("open_pr", open_pr),
    ]:
        g.add_node(name, partial(module.run, deps=deps))

    def entry(state: AgentState) -> str:
        status = deps.store.get(state["incident_id"]).status
        if status == IncidentStatus.RECEIVED:
            return "investigate"
        if status == IncidentStatus.APPROVED:
            return "apply_patch"
        return "stop"

    g.add_conditional_edges(
        START, entry, {"investigate": "investigate", "apply_patch": "apply_patch", "stop": END}
    )
    g.add_conditional_edges("investigate", _outcome, {"ok": "root_cause", "stop": END})
    g.add_edge("root_cause", "generate_patch")
    g.add_conditional_edges(
        "generate_patch", _outcome, {"patch": "policy_gate", "no_patch": END, "stop": END}
    )
    g.add_conditional_edges(
        "policy_gate", _outcome, {"approved": "apply_patch", "await_approval": END, "denied": END}
    )
    g.add_conditional_edges(
        "apply_patch", _outcome, {"pushed": "verify", "no_delivery": END, "stop": END}
    )
    g.add_conditional_edges(
        "verify",
        _outcome,
        {"passed": "open_pr", "retry": "generate_patch", "failed": END, "stop": END},
    )
    g.add_edge("open_pr", END)
    return g.compile()
