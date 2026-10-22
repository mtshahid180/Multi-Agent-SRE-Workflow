"""Runs the graph for one incident at a time, turning crashes into a failed status."""

import logging
import threading

from app.agent.graph import build_graph
from app.agent.nodes.common import fail
from app.agent.state import Deps

log = logging.getLogger("ai_sre_agent")


class AgentRunner:
    def __init__(self, deps: Deps):
        self.deps = deps
        self._graph = build_graph(deps)
        self._running: set[str] = set()
        self._lock = threading.Lock()

    def run(self, incident_id: str) -> None:
        with self._lock:
            if incident_id in self._running:
                return
            self._running.add(incident_id)
        try:
            self._graph.invoke({"incident_id": incident_id}, {"recursion_limit": 40})
        except Exception as exc:  # noqa: BLE001 - never leave an incident stuck mid-flight
            log.exception("Agent run crashed for incident %s", incident_id)
            fail(self.deps, incident_id, f"Internal error: {type(exc).__name__}")
        finally:
            with self._lock:
                self._running.discard(incident_id)
