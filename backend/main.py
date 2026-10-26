"""FastAPI server: intake forms, records, rules-based matching, the AI agent endpoints, and staff sign-in.

Every /api route except sign-in requires a session and a role permission (see auth.py).

Caseload limits: a caseworker sees and works only the children and foster homes assigned to them
(`caseworker.assigned_caseworker` = their username). Supervisors see every record. To match one of their own
children, a caseworker sees every home's rules-engine result (name, score, factors, blockers) but can't open the full
file of a home that isn't on their caseload."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic_ai.exceptions import RunTimedOut, UnexpectedModelBehavior, UsageLimitExceeded

from . import agent
from . import auth
from .auth import Accounts, Role, User, bootstrap_admin, build_router, client_ip, ensure_demo_accounts
from .models import (
    CARE_LEVEL_LABELS,
    ChildRecord,
    FosterChildIntake,
    FosterParentIntake,
    ParentRecord,
    PlacementDecision,
    PlacementRecord,
    PlacementStatus,
)
from .scoring import WEIGHTS, BoardRow, Compatibility, evaluate, placement_board, rank_parents
from .seed import seed
from .storage import DEFAULT_DB, Store

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

store = Store(os.getenv("FOSTERMATCH_DB", DEFAULT_DB))
if store.is_empty() and os.getenv("FOSTERMATCH_SEED", "1") == "1":
    seed(store)

accounts = Accounts(store.db)
bootstrap_admin(accounts)
if auth.DEMO_SIGNIN:
    ensure_demo_accounts(accounts)


def _link_legacy_assignments() -> None:
    """Older records named their caseworker in free text ("K. Osei"). Link them to the matching staff account."""
    staff = accounts.list()

    def username_for(name: str) -> str | None:
        n = " ".join(name.lower().split())
        for u in staff:
            parts = u.full_name.lower().split()
            if n in (u.username.lower(), " ".join(parts)) or (len(parts) > 1 and n == f"{parts[0][0]}. {parts[-1]}"):
                return u.username
        return None

    for record, save, model in [(r, store.save_parent, FosterParentIntake) for r in store.list_parents()] + \
                                [(r, store.save_child, FosterChildIntake) for r in store.list_children()]:
        current = record.caseworker.assigned_caseworker
        linked = username_for(current) if current else None
        if linked and linked != current:
            record.caseworker.assigned_caseworker = linked
            save(model.model_validate(record.model_dump()), record.id)


_link_legacy_assignments()

app = FastAPI(title="Hearth & Horizon: Foster Placement Matching")
log = logging.getLogger("fostermatch")
auth_router, require = build_router(accounts)
app.include_router(auth_router)

can_read = Depends(require("records:read"))
can_write = Depends(require("records:write"))
can_delete = Depends(require("records:delete"))
can_run_ai = Depends(require("ai:run"))
can_place = Depends(require("placements:approve"))
can_audit = Depends(require("audit:read"))

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def access_control_log(request: Request, call_next):
    path = request.url.path
    # Cross-site request forgery: SameSite=Strict cookies already stop it; also refuse writes from other origins.
    origin = request.headers.get("origin")
    if request.method in UNSAFE and origin and origin.split("://", 1)[-1] != request.headers.get("host"):
        accounts.log("cross-origin request blocked", status=403, ip=client_ip(request), detail=f"{request.method} {path} from {origin}")
        return JSONResponse({"detail": "Cross-origin request refused."}, status_code=403)
    response = await call_next(request)
    # Who touched which data: every case-data call is written to the access log (sign-in events log themselves).
    if path.startswith("/api/") and not path.startswith(("/api/auth/", "/api/meta")):
        user = getattr(request.state, "user", None)
        if user or response.status_code != 401:
            accounts.log(f"{request.method} {path}", user, status=response.status_code, ip=client_ip(request))
    return response


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


# ---------- caseload limits ----------

def on_caseload(user: User, record) -> bool:
    return user.role != Role.CASEWORKER or record.caseworker.assigned_caseworker.lower() == user.username.lower()


def _own(user: User, record, request: Request | None = None):
    """The record, if it's on this user's caseload; otherwise 403 (and an access-log entry)."""
    if not on_caseload(user, record):
        accounts.log("caseload denied", user, status=403, ip=client_ip(request) if request else None,
                     detail=f"{record.id} is assigned to someone else")
        raise HTTPException(403, "This record is not on your caseload.")
    return record


def _load(kind: str, record_id: str):
    try:
        return store.get_parent(record_id) if kind == "parent" else store.get_child(record_id)
    except KeyError:
        _missing(record_id)


def _assign(user: User, intake, existing=None):
    """Caseworkers can only file records to themselves and can't reassign them; supervisors assign to any staff."""
    if user.role == Role.CASEWORKER:
        intake.caseworker.assigned_caseworker = existing.caseworker.assigned_caseworker if existing else user.username
        return intake
    assigned = intake.caseworker.assigned_caseworker.strip()
    if assigned and assigned not in {u.username for u in _assignable_staff()}:
        raise HTTPException(400, f"'{assigned}' is not an active caseworker or supervisor username.")
    return intake


def _assignable_staff() -> list[User]:
    return [u for u in accounts.list() if u.active and u.role in (Role.CASEWORKER, Role.SUPERVISOR)]


@app.get("/api/staff")
def staff(user: User = can_read) -> list[dict]:
    """Who records can be assigned to (for the caseworker dropdown and for showing names)."""
    return [{"username": u.username, "full_name": u.full_name, "role": u.role.value} for u in _assignable_staff()]


# ---------- metadata ----------

@app.get("/api/meta", dependencies=[can_read])
def meta() -> dict:
    return {"care_levels": {int(k): v for k, v in CARE_LEVEL_LABELS.items()}, "weights": WEIGHTS, "model": agent.MODEL_NAME}


# ---------- foster parents ----------

@app.get("/api/parents")
def list_parents(user: User = can_read) -> list[ParentRecord]:
    return [p for p in store.list_parents() if on_caseload(user, p)]


@app.post("/api/parents", status_code=201)
def create_parent(intake: FosterParentIntake, user: User = can_write) -> ParentRecord:
    return store.save_parent(_assign(user, intake))


@app.get("/api/parents/{parent_id}")
def get_parent(parent_id: str, request: Request, user: User = can_read) -> ParentRecord:
    return _own(user, _load("parent", parent_id), request)


@app.put("/api/parents/{parent_id}")
def update_parent(parent_id: str, intake: FosterParentIntake, request: Request, user: User = can_write) -> ParentRecord:
    existing = _own(user, _load("parent", parent_id), request)
    return store.save_parent(_assign(user, intake, existing), parent_id)


# ---------- foster children ----------

@app.get("/api/children")
def list_children(user: User = can_read) -> list[ChildRecord]:
    return [c for c in store.list_children() if on_caseload(user, c)]


@app.post("/api/children", status_code=201)
def create_child(intake: FosterChildIntake, user: User = can_write) -> ChildRecord:
    return store.save_child(_assign(user, intake))


@app.get("/api/children/{child_id}")
def get_child(child_id: str, request: Request, user: User = can_read) -> ChildRecord:
    return _own(user, _load("child", child_id), request)


@app.put("/api/children/{child_id}")
def update_child(child_id: str, intake: FosterChildIntake, request: Request, user: User = can_write) -> ChildRecord:
    existing = _own(user, _load("child", child_id), request)
    return store.save_child(_assign(user, intake, existing), child_id)


@app.delete("/api/records/{record_id}", status_code=204, dependencies=[can_delete])
def delete_record(record_id: str) -> None:
    store.delete(record_id)


# ---------- rules-based matching (instant, no AI) ----------

@app.get("/api/match/child/{child_id}")
def match_child(child_id: str, request: Request, user: User = can_read) -> list[Compatibility]:
    child = _own(user, _load("child", child_id), request)
    return rank_parents(child, store.list_parents())


@app.get("/api/match/pair/{parent_id}/{child_id}")
def match_pair(parent_id: str, child_id: str, request: Request, user: User = can_read) -> Compatibility:
    child = _own(user, _load("child", child_id), request)
    return evaluate(_load("parent", parent_id), child)


@app.get("/api/match/board")
def board(user: User = can_read) -> list[BoardRow]:
    # Proposals are computed over the whole caseload (so two workers aren't offered the same bed); rows are filtered.
    children = store.list_children()
    mine = {c.id for c in children if on_caseload(user, c)}
    return [row for row in placement_board(children, store.list_parents()) if row.child_id in mine]


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


async def _run_ai(subject_id: str, run_type: str, prepared: agent.PreparedRun, runner, force: bool, user: User) -> dict:
    # One run per record at a time: a double-click waits and then reuses the first result instead of paying twice.
    async with _subject_locks.setdefault(f"{run_type}:{subject_id}", asyncio.Lock()):
        cached = store.get_report(subject_id, run_type)
        if cached and not force and cached["meta"].get("fingerprint") == prepared.fingerprint:
            store.log_ai_run(subject_id, run_type, "reused", {"fingerprint": prepared.fingerprint, "user": user.username})
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
                store.log_ai_run(subject_id, run_type, outcome, {"fingerprint": prepared.fingerprint, "error": type(e).__name__, "user": user.username,
                                                                 "withheld_fields": prepared.screening.withheld_fields})
                raise HTTPException(status, message) from None

        store.save_report(subject_id, run_type, output.model_dump(mode="json"), meta)
        store.log_ai_run(subject_id, run_type, "ok", {**meta, "user": user.username})
        return {**store.get_report(subject_id, run_type), "reused": False}


@app.post("/api/ai/summary/{kind}/{record_id}")
async def ai_summary(kind: str, record_id: str, request: Request, force: bool = False, user: User = can_run_ai) -> dict:
    if kind not in ("parent", "child"):
        raise HTTPException(404, "Unknown record type")
    _own(user, _load(kind, record_id), request)
    try:
        prepared = agent.prepare_summary(store, kind, record_id)
    except KeyError:
        _missing(record_id)
    return await _run_ai(record_id, "summary", prepared, agent.run_summary, force, user)


@app.post("/api/ai/match/{child_id}")
async def ai_match(child_id: str, request: Request, force: bool = False, user: User = can_run_ai) -> dict:
    _own(user, _load("child", child_id), request)
    try:
        prepared = agent.prepare_match(store, child_id)
    except KeyError:
        _missing(child_id)
    return await _run_ai(child_id, "match", prepared, agent.run_match, force, user)


@app.get("/api/ai/usage", dependencies=[can_audit])
def ai_usage() -> dict:
    return store.ai_usage()


@app.get("/api/ai/{report_type}/{record_id}")
def cached_report(report_type: str, record_id: str, request: Request, user: User = can_read) -> dict:
    if report_type not in ("summary", "match"):
        raise HTTPException(404, "Unknown report type")
    _own(user, _load("parent" if record_id.startswith("P-") else "child", record_id), request)
    report = store.get_report(record_id, report_type)
    if report is None:
        raise HTTPException(404, "No AI report yet")
    return report


# ---------- placements (human decision) ----------

@app.post("/api/placements", status_code=201)
def place(decision: PlacementDecision, user: User = can_place) -> PlacementRecord:
    decision.decided_by = f"{user.full_name} ({user.username})"  # from the session, not the request body
    try:
        result = evaluate(store.get_parent(decision.parent_id), store.get_child(decision.child_id))
    except KeyError as e:
        _missing(str(e))
    if not result.eligible:
        raise HTTPException(409, "Home is not eligible: " + " ".join(result.blockers))
    if store.get_child(decision.child_id).caseworker.placement_status != PlacementStatus.AWAITING:
        raise HTTPException(409, "This child is not awaiting placement. Update the child's placement status first.")
    return store.record_placement(decision)


@app.get("/api/placements")
def placements(user: User = can_read) -> list[PlacementRecord]:
    if user.role != Role.CASEWORKER:
        return store.list_placements()
    mine = {c.id for c in store.list_children() if on_caseload(user, c)}
    return [p for p in store.list_placements() if p.child_id in mine]


# ---------- frontend ----------

app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")
