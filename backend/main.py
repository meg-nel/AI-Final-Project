"""FastAPI server: intake forms, records, rules-based matching, and the AI agent endpoints."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic_ai.exceptions import RunTimedOut, UnexpectedModelBehavior, UsageLimitExceeded

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
log = logging.getLogger("fostermatch")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.update({
        # Scripts only from this server: stops injected markup from running code even if escaping were missed.
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                   "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store" if request.url.path.startswith("/api/") else "no-cache",
    })
    return response


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

AI_MAX_CONCURRENT = int(os.getenv("FOSTERMATCH_AI_CONCURRENCY", "3"))
AI_MAX_PER_MINUTE = int(os.getenv("FOSTERMATCH_AI_PER_MINUTE", "20"))
_ai_slots = asyncio.Semaphore(AI_MAX_CONCURRENT)
_ai_recent: deque[float] = deque()
_subject_locks: dict[str, asyncio.Lock] = {}
AI_FAILURES = [  # most specific first
    (UsageLimitExceeded, ("stopped_limit", 502, "The AI agent hit its step limit and was stopped.")),
    (RunTimedOut, ("timeout", 504, "The AI agent took too long and was stopped.")),
    (TimeoutError, ("timeout", 504, "The AI agent took too long and was stopped.")),
    (UnexpectedModelBehavior, ("rejected", 502, "The AI answer failed the safety and accuracy checks, so it was discarded.")),
]


def _rate_limit() -> None:
    """Global cap on model calls per minute, so a stuck client or script can't run up the bill."""
    now = time.monotonic()
    while _ai_recent and now - _ai_recent[0] > 60:
        _ai_recent.popleft()
    if len(_ai_recent) >= AI_MAX_PER_MINUTE:
        raise HTTPException(429, "AI rate limit reached. Try again in a minute.")
    _ai_recent.append(now)


async def _run_ai(subject_id: str, run_type: str, prepared: agent.PreparedRun, runner, force: bool) -> dict:
    # One run per record at a time: a double-click waits and then reuses the first result instead of paying twice.
    async with _subject_locks.setdefault(f"{run_type}:{subject_id}", asyncio.Lock()):
        cached = store.get_report(subject_id, run_type)
        if cached and not force and cached["meta"].get("fingerprint") == prepared.fingerprint:
            store.log_ai_run(subject_id, run_type, "reused", {"fingerprint": prepared.fingerprint})
            return {**cached, "reused": True}

        _rate_limit()
        async with _ai_slots:
            try:
                output, meta = await runner(prepared)
            except Exception as e:  # never echo provider errors (they can include request content) to the browser
                outcome, status, message = next(
                    (v for cls, v in AI_FAILURES if isinstance(e, cls)),
                    ("error", 502, "The AI service is unavailable right now."),
                )
                log.warning("AI %s run for %s failed: %r", run_type, subject_id, e)
                store.log_ai_run(subject_id, run_type, outcome, {"fingerprint": prepared.fingerprint, "error": type(e).__name__,
                                                                 "withheld_fields": prepared.screening.withheld_fields})
                raise HTTPException(status, message) from None

        store.save_report(subject_id, run_type, output.model_dump(mode="json"), meta)
        store.log_ai_run(subject_id, run_type, "ok", meta)
        return {**store.get_report(subject_id, run_type), "reused": False}


@app.post("/api/ai/summary/{kind}/{record_id}")
async def ai_summary(kind: str, record_id: str, force: bool = False) -> dict:
    if kind not in ("parent", "child"):
        raise HTTPException(404, "Unknown record type")
    try:
        prepared = agent.prepare_summary(store, kind, record_id)
    except KeyError:
        _missing(record_id)
    return await _run_ai(record_id, "summary", prepared, agent.run_summary, force)


@app.post("/api/ai/match/{child_id}")
async def ai_match(child_id: str, force: bool = False) -> dict:
    try:
        prepared = agent.prepare_match(store, child_id)
    except KeyError:
        _missing(child_id)
    return await _run_ai(child_id, "match", prepared, agent.run_match, force)


@app.get("/api/ai/usage")
def ai_usage() -> dict:
    return store.ai_usage()


@app.get("/api/ai/{report_type}/{record_id}")
def cached_report(report_type: str, record_id: str) -> dict:
    if report_type not in ("summary", "match"):
        raise HTTPException(404, "Unknown report type")
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
