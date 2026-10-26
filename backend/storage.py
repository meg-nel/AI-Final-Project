"""SQLite persistence. Each intake is stored as one validated JSON document, keyed by id."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .models import (
    ChildRecord,
    FosterChildIntake,
    FosterParentIntake,
    ParentRecord,
    PlacementDecision,
    PlacementRecord,
    PlacementStatus,
)

Kind = Literal["parent", "child"]
DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "fostermatch.db"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _Result:
    """Rows fetched while the lock was held, so no cursor is shared between threads."""

    def __init__(self, cursor: sqlite3.Cursor):
        self.rows = cursor.fetchall() if cursor.description else []
        self.rowcount = cursor.rowcount
        self.lastrowid = cursor.lastrowid

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class LockedConnection:
    """One SQLite connection shared by FastAPI's worker threads. Every statement (and reading its rows) runs under a
    lock: without it, concurrent requests corrupt the connection ("bad parameter or other API misuse")."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.RLock()

    def execute(self, sql: str, params=()) -> _Result:
        with self._lock:
            return _Result(self._conn.execute(sql, params))

    def executescript(self, script: str) -> None:
        with self._lock:
            self._conn.executescript(script)

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()


class Store:
    def __init__(self, path: Path | str = DEFAULT_DB):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = LockedConnection(sqlite3.connect(str(path), check_same_thread=False))
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS records (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ai_reports (
                subject_id TEXT NOT NULL, report_type TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY (subject_id, report_type));
            CREATE TABLE IF NOT EXISTS placements (
                id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ai_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, subject_id TEXT NOT NULL,
                run_type TEXT NOT NULL, outcome TEXT NOT NULL, cost_usd REAL NOT NULL DEFAULT 0, meta TEXT NOT NULL);
            """
        )

    # ---------- intakes ----------

    def _save(self, kind: Kind, payload: dict, record_id: str | None) -> str:
        now = _now().isoformat()
        if record_id is None:
            record_id = f"{'P' if kind == 'parent' else 'C'}-{uuid.uuid4().hex[:8].upper()}"
            self.db.execute(
                "INSERT INTO records VALUES (?, ?, ?, ?, ?)", (record_id, kind, json.dumps(payload), now, now)
            )
        else:
            cur = self.db.execute(
                "UPDATE records SET data = ?, updated_at = ? WHERE id = ? AND kind = ?",
                (json.dumps(payload), now, record_id, kind),
            )
            if cur.rowcount == 0:
                raise KeyError(record_id)
        self.db.commit()
        return record_id

    def save_parent(self, intake: FosterParentIntake, record_id: str | None = None) -> ParentRecord:
        rid = self._save("parent", intake.model_dump(mode="json", exclude_computed_fields=True), record_id)
        return self.get_parent(rid)

    def save_child(self, intake: FosterChildIntake, record_id: str | None = None) -> ChildRecord:
        rid = self._save("child", intake.model_dump(mode="json", exclude_computed_fields=True), record_id)
        return self.get_child(rid)

    def _rows(self, kind: Kind, record_id: str | None = None):
        sql = "SELECT id, data, created_at, updated_at FROM records WHERE kind = ?"
        args: tuple = (kind,)
        if record_id:
            sql += " AND id = ?"
            args += (record_id,)
        return self.db.execute(sql + " ORDER BY created_at", args).fetchall()

    @staticmethod
    def _hydrate(model, row):
        rid, data, created, updated = row
        return model.model_validate({**json.loads(data), "id": rid, "created_at": created, "updated_at": updated})

    def get_parent(self, record_id: str) -> ParentRecord:
        rows = self._rows("parent", record_id)
        if not rows:
            raise KeyError(record_id)
        return self._hydrate(ParentRecord, rows[0])

    def get_child(self, record_id: str) -> ChildRecord:
        rows = self._rows("child", record_id)
        if not rows:
            raise KeyError(record_id)
        return self._hydrate(ChildRecord, rows[0])

    def list_parents(self) -> list[ParentRecord]:
        return [self._hydrate(ParentRecord, r) for r in self._rows("parent")]

    def list_children(self) -> list[ChildRecord]:
        return [self._hydrate(ChildRecord, r) for r in self._rows("child")]

    def delete(self, record_id: str) -> None:
        self.db.execute("DELETE FROM records WHERE id = ?", (record_id,))
        self.db.execute("DELETE FROM ai_reports WHERE subject_id = ?", (record_id,))
        self.db.commit()

    def is_empty(self) -> bool:
        return self.db.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0

    # ---------- AI reports (cached so caseworkers can revisit without re-running the model) ----------

    def save_report(self, subject_id: str, report_type: str, report: dict, meta: dict) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO ai_reports VALUES (?, ?, ?, ?)",
            (subject_id, report_type, json.dumps({"report": report, "meta": meta}), _now().isoformat()),
        )
        self.db.commit()

    def get_report(self, subject_id: str, report_type: str) -> dict | None:
        row = self.db.execute(
            "SELECT data, created_at FROM ai_reports WHERE subject_id = ? AND report_type = ?",
            (subject_id, report_type),
        ).fetchone()
        if not row:
            return None
        data = json.loads(row[0])
        if "report" not in data:  # saved by an earlier version without run metadata: treat as stale
            return None
        return {**data, "generated_at": row[1]}

    # ---------- AI audit log: every run, its outcome, what was withheld, and what it cost ----------

    def log_ai_run(self, subject_id: str, run_type: str, outcome: str, meta: dict) -> None:
        self.db.execute(
            "INSERT INTO ai_runs (created_at, subject_id, run_type, outcome, cost_usd, meta) VALUES (?, ?, ?, ?, ?, ?)",
            (_now().isoformat(), subject_id, run_type, outcome, meta.get("cost_usd", 0), json.dumps(meta)),
        )
        self.db.commit()

    def ai_usage(self, limit: int = 50) -> dict:
        totals = self.db.execute(
            "SELECT COUNT(*), COALESCE(SUM(cost_usd), 0), SUM(outcome = 'ok'), SUM(outcome = 'reused') FROM ai_runs"
        ).fetchone()
        rows = self.db.execute(
            "SELECT created_at, subject_id, run_type, outcome, cost_usd, meta FROM ai_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return {
            "runs": totals[0], "total_cost_usd": round(totals[1], 6), "model_runs": totals[2] or 0, "reused": totals[3] or 0,
            "recent": [{"created_at": r[0], "subject_id": r[1], "run_type": r[2], "outcome": r[3], "cost_usd": r[4],
                        **{k: v for k, v in json.loads(r[5]).items() if k in ("requests", "seconds", "withheld_fields", "error")}}
                       for r in rows],
        }

    # ---------- placements (the human decision, recorded) ----------

    def record_placement(self, decision: PlacementDecision) -> PlacementRecord:
        """Place the child (and any linked siblings marked place-together) and reserve the home's beds."""
        child = self.get_child(decision.child_id)
        parent = self.get_parent(decision.parent_id)
        group = [child] + [
            self.get_child(s.sibling_child_id)
            for s in child.siblings
            if s.place_together and s.sibling_child_id and self._exists(s.sibling_child_id)
        ]
        group = [c for c in group if c.caseworker.placement_status != PlacementStatus.PLACED]
        for member in group:
            member.caseworker.placement_status = PlacementStatus.PLACED
            self.save_child(FosterChildIntake.model_validate(member.model_dump()), member.id)
        parent.home.available_beds = max(0, parent.home.available_beds - max(len(group), child.sibling_group_size))
        self.save_parent(FosterParentIntake.model_validate(parent.model_dump()), parent.id)

        record = PlacementRecord(**decision.model_dump(), id=f"PL-{uuid.uuid4().hex[:8].upper()}", created_at=_now())
        self.db.execute(
            "INSERT INTO placements VALUES (?, ?, ?)",
            (record.id, record.model_dump_json(), record.created_at.isoformat()),
        )
        self.db.commit()
        return record

    def list_placements(self) -> list[PlacementRecord]:
        rows = self.db.execute("SELECT data FROM placements ORDER BY created_at DESC").fetchall()
        return [PlacementRecord.model_validate_json(r[0]) for r in rows]

    def _exists(self, record_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM records WHERE id = ?", (record_id,)).fetchone() is not None
