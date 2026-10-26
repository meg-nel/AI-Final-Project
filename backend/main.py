"""FastAPI server: intake forms, records, rules-based matching, and the AI agent endpoints."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic_ai.exceptions import AgentRunError, ModelHTTPError

from . import agent
from .models import (
    CARE_LEVEL_LABELS,
    ChildRecord,
    FosterChildIntake,
    FosterParentIntake,
    ParentRecord,
    PlacementDecision,
    PlacementRecord,
)
from .scoring import WEIGHTS, BoardRow, Compatibility, evaluate, placement_board, rank_parents
from .seed import seed
from .storage import DEFAULT_DB, Store

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

store = Store(os.getenv("FOSTERMATCH_DB", DEFAULT_DB))
if store.is_empty() and os.getenv("FOSTERMATCH_SEED", "1") == "1":
    seed(store)

app = FastAPI(title="Hearth & Horizon: Foster Placement Matching")


def _missing(record_id: str):
    raise HTTPException(404, f"No record with id {record_id}")


# ---------- metadata ----------

@app.get("/api/meta")
def meta() -> dict:
    return {"care_levels": {int(k): v for k, v in CARE_LEVEL_LABELS.items()}, "weights": WEIGHTS, "model": agent.MODEL_NAME}


# ---------- foster parents ----------

@app.get("/api/parents")
def list_parents() -> list[ParentRecord]:
    return store.list_parents()


@app.post("/api/parents", status_code=201)
def create_parent(intake: FosterParentIntake) -> ParentRecord:
    return store.save_parent(intake)


@app.get("/api/parents/{parent_id}")
def get_parent(parent_id: str) -> ParentRecord:
    try:
        return store.get_parent(parent_id)
    except KeyError:
        _missing(parent_id)


@app.put("/api/parents/{parent_id}")
def update_parent(parent_id: str, intake: FosterParentIntake) -> ParentRecord:
    try:
        return store.save_parent(intake, parent_id)
    except KeyError:
        _missing(parent_id)


# ---------- foster children ----------

@app.get("/api/children")
def list_children() -> list[ChildRecord]:
    return store.list_children()


@app.post("/api/children", status_code=201)
def create_child(intake: FosterChildIntake) -> ChildRecord:
    return store.save_child(intake)


@app.get("/api/children/{child_id}")
def get_child(child_id: str) -> ChildRecord:
    try:
        return store.get_child(child_id)
    except KeyError:
        _missing(child_id)


@app.put("/api/children/{child_id}")
def update_child(child_id: str, intake: FosterChildIntake) -> ChildRecord:
    try:
        return store.save_child(intake, child_id)
    except KeyError:
        _missing(child_id)


@app.delete("/api/records/{record_id}", status_code=204)
def delete_record(record_id: str) -> None:
    store.delete(record_id)


# ---------- rules-based matching (instant, no AI) ----------

@app.get("/api/match/child/{child_id}")
def match_child(child_id: str) -> list[Compatibility]:
    try:
        return rank_parents(store.get_child(child_id), store.list_parents())
    except KeyError:
        _missing(child_id)


@app.get("/api/match/pair/{parent_id}/{child_id}")
def match_pair(parent_id: str, child_id: str) -> Compatibility:
    try:
        return evaluate(store.get_parent(parent_id), store.get_child(child_id))
    except KeyError as e:
        _missing(str(e))


@app.get("/api/match/board")
def board() -> list[BoardRow]:
    return placement_board(store.list_children(), store.list_parents())


# ---------- AI agent ----------

async def _run_ai(coro):
    try:
        return await coro
    except (ModelHTTPError, AgentRunError, RuntimeError) as e:
        raise HTTPException(502, f"AI agent failed: {e}")


@app.post("/api/ai/summary/{kind}/{record_id}")
async def ai_summary(kind: str, record_id: str) -> dict:
    try:
        record = store.get_parent(record_id) if kind == "parent" else store.get_child(record_id)
    except KeyError:
        _missing(record_id)
    fn = agent.summarize_parent if kind == "parent" else agent.summarize_child
    summary = await _run_ai(fn(record))
    store.save_report(record_id, "summary", summary.model_dump(mode="json"))
    return store.get_report(record_id, "summary")


@app.post("/api/ai/match/{child_id}")
async def ai_match(child_id: str) -> dict:
    try:
        store.get_child(child_id)
    except KeyError:
        _missing(child_id)
    report = await _run_ai(agent.recommend_homes(store, child_id))
    store.save_report(child_id, "match", report.model_dump(mode="json"))
    return store.get_report(child_id, "match")


@app.get("/api/ai/{report_type}/{record_id}")
def cached_report(report_type: str, record_id: str) -> dict:
    report = store.get_report(record_id, report_type)
    if report is None:
        raise HTTPException(404, "No AI report yet")
    return report


# ---------- placements (human decision) ----------

@app.post("/api/placements", status_code=201)
def place(decision: PlacementDecision) -> PlacementRecord:
    try:
        result = evaluate(store.get_parent(decision.parent_id), store.get_child(decision.child_id))
    except KeyError as e:
        _missing(str(e))
    if not result.eligible:
        raise HTTPException(409, "Home is not eligible: " + " ".join(result.blockers))
    return store.record_placement(decision)


@app.get("/api/placements")
def placements() -> list[PlacementRecord]:
    return store.list_placements()


# ---------- frontend ----------

app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")
