"""Dependencies shared by graph nodes, and the (small) LangGraph state."""

from dataclasses import dataclass
from typing import TypedDict

from app.config import Settings
from app.integrations.github_client import RepoRouter
from app.integrations.llm_client import LLMClient
from app.integrations.test_runner import CIRunner
from app.storage.incidents import IncidentStore


class AgentState(TypedDict, total=False):
    """Incident data lives in the database; the graph only carries routing hints."""

    incident_id: str
    outcome: str


@dataclass
class Deps:
    settings: Settings
    store: IncidentStore
    llm: LLMClient
    repos: RepoRouter
    ci: CIRunner
