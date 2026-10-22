"""SQLite persistence for incidents, audit events and webhook deliveries."""

import json
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

from app.schemas.incident import Incident, utcnow


class IncidentNotFoundError(KeyError):
    pass


def _sqlite_path(database_url: str) -> str:
    if not database_url.startswith("sqlite:///"):
        raise ValueError("Only sqlite:/// URLs are supported in this version.")
    return database_url.removeprefix("sqlite:///")


class IncidentStore:
    def __init__(self, database_url: str):
        self._path = _sqlite_path(database_url)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._memory_conn = (
            sqlite3.connect(":memory:", check_same_thread=False)
            if self._path == ":memory:"
            else None
        )
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return self._memory_conn or sqlite3.connect(self._path, timeout=30)

    def _run(self, sql: str, params: tuple = (), fetch: str | None = None):
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(sql, params)
                result = cur.fetchall() if fetch == "all" else cur.fetchone() if fetch else None
                conn.commit()
                return result
            finally:
                if self._memory_conn is None:
                    conn.close()

    def _init_schema(self) -> None:
        for ddl in (
            "CREATE TABLE IF NOT EXISTS incidents (id TEXT PRIMARY KEY, created_at TEXT,"
            " updated_at TEXT, status TEXT, repository TEXT, data TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " incident_id TEXT, ts TEXT, actor TEXT, type TEXT, detail TEXT)",
            "CREATE TABLE IF NOT EXISTS webhook_deliveries (delivery_id TEXT PRIMARY KEY,"
            " received_at TEXT)",
        ):
            self._run(ddl)

    # -- incidents -------------------------------------------------------
    def create(self, incident: Incident) -> Incident:
        self._run(
            "INSERT INTO incidents VALUES (?,?,?,?,?,?)",
            (
                incident.id,
                incident.created_at.isoformat(),
                incident.updated_at.isoformat(),
                incident.status.value,
                incident.request.repository,
                incident.model_dump_json(),
            ),
        )
        return incident

    def get(self, incident_id: str) -> Incident:
        row = self._run("SELECT data FROM incidents WHERE id=?", (incident_id,), fetch="one")
        if row is None:
            raise IncidentNotFoundError(incident_id)
        return Incident.model_validate_json(row[0])

    def save(self, incident: Incident) -> Incident:
        incident.updated_at = utcnow()
        self._run(
            "UPDATE incidents SET updated_at=?, status=?, data=? WHERE id=?",
            (
                incident.updated_at.isoformat(),
                incident.status.value,
                incident.model_dump_json(),
                incident.id,
            ),
        )
        return incident

    def update(self, incident_id: str, mutator: Callable[[Incident], None]) -> Incident:
        """Atomic read-modify-write (serialised by an in-process lock)."""
        with self._lock:
            incident = self.get(incident_id)
            mutator(incident)
            return self.save(incident)

    def recent(self, limit: int = 50) -> list[Incident]:
        rows = self._run(
            "SELECT data FROM incidents ORDER BY created_at DESC LIMIT ?", (limit,), fetch="all"
        )
        return [Incident.model_validate_json(r[0]) for r in rows]

    # -- audit trail -----------------------------------------------------
    def add_event(self, incident_id: str, actor: str, type_: str, detail: dict | None = None):
        self._run(
            "INSERT INTO events (incident_id, ts, actor, type, detail) VALUES (?,?,?,?,?)",
            (incident_id, utcnow().isoformat(), actor, type_, json.dumps(detail or {})),
        )

    def events(self, incident_id: str) -> list[dict]:
        rows = self._run(
            "SELECT ts, actor, type, detail FROM events WHERE incident_id=? ORDER BY id",
            (incident_id,),
            fetch="all",
        )
        return [{"ts": r[0], "actor": r[1], "type": r[2], "detail": json.loads(r[3])} for r in rows]

    # -- webhook replay protection ---------------------------------------
    def record_delivery(self, delivery_id: str) -> bool:
        """Returns False if this delivery id was already processed."""
        with self._lock:
            row = self._run(
                "SELECT 1 FROM webhook_deliveries WHERE delivery_id=?", (delivery_id,), fetch="one"
            )
            if row:
                return False
            self._run(
                "INSERT INTO webhook_deliveries VALUES (?,?)", (delivery_id, utcnow().isoformat())
            )
            return True
