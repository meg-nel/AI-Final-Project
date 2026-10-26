"""Sign-in, sessions, and role-based access control."""

import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend import agent
from backend import main as server
from backend.auth import Role, _token_hash
from tests.conftest import PASSWORD, signed_in

PUBLIC = {("POST", "/api/auth/login"), ("POST", "/api/auth/logout")}


def api_routes():
    for route in server.app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api/"):
            for method in route.methods - {"HEAD", "OPTIONS"}:
                if (method, route.path) not in PUBLIC:
                    yield method, re.sub(r"\{[^}]+\}", "1", route.path)


@pytest.mark.parametrize("method,path", list(api_routes()))
def test_every_api_route_requires_sign_in(method, path):
    r = TestClient(server.app).request(method, path, json={})
    assert r.status_code == 401, f"{method} {path} answered {r.status_code} without a session"


def child_id():
    return server.store.list_children()[0].id


def parent_id():
    return server.store.list_parents()[0].id


# ---------- role matrix ----------

def test_caseworker_permissions(caseworker):
    assert caseworker.get("/api/children").status_code == 200
    assert caseworker.get(f"/api/match/child/{child_id()}").status_code == 200
    assert caseworker.post("/api/placements", json={"child_id": child_id(), "parent_id": parent_id()}).status_code == 403
    assert caseworker.delete(f"/api/records/{child_id()}").status_code == 403
    assert caseworker.get("/api/users").status_code == 403
    assert caseworker.get("/api/audit/access").status_code == 403
    assert caseworker.get("/api/ai/usage").status_code == 403


def test_supervisor_permissions(supervisor):
    assert supervisor.get("/api/children").status_code == 200
    assert supervisor.get("/api/audit/access").status_code == 200
    assert supervisor.get("/api/ai/usage").status_code == 200
    assert supervisor.get("/api/users").status_code == 403


def test_admin_cannot_open_case_records(admin, monkeypatch):
    monkeypatch.setattr(agent, "run_match", lambda *_: pytest.fail("admin must not reach the model"))
    assert admin.get("/api/users").status_code == 200
    assert admin.get("/api/audit/access").status_code == 200
    for path in ("/api/children", "/api/parents", f"/api/children/{child_id()}", "/api/match/board", "/api/placements"):
        assert admin.get(path).status_code == 403, path
    assert admin.post(f"/api/ai/match/{child_id()}").status_code == 403


def test_permission_denials_are_logged(caseworker):
    caseworker.get("/api/users")
    log = server.accounts.recent_log(20)
    assert any(e["action"] == "permission denied" and e["username"] == "casey" for e in log)


# ---------- sign-in ----------

def test_wrong_password_and_unknown_user_get_the_same_answer():
    c = TestClient(server.app)
    a = c.post("/api/auth/login", json={"username": "casey", "password": "nope-nope-nope"})
    b = c.post("/api/auth/login", json={"username": "nobody", "password": "nope-nope-nope"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_account_locks_after_repeated_failures():
    server.accounts.create("lockme", "Lock Me", Role.CASEWORKER, PASSWORD, must_change=False)
    c = TestClient(server.app)
    for _ in range(5):
        c.post("/api/auth/login", json={"username": "lockme", "password": "wrong-password-x"})
    assert c.post("/api/auth/login", json={"username": "lockme", "password": PASSWORD}).status_code == 401
    assert any(e["detail"] == "locked" for e in server.accounts.recent_log(5))


def test_session_cookie_flags_and_token_is_hashed_at_rest():
    c = TestClient(server.app)
    r = c.post("/api/auth/login", json={"username": "casey", "password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    token = c.cookies["fm_session"]
    rows = [row[0] for row in server.store.db.execute("SELECT token_hash FROM sessions").fetchall()]
    assert token not in rows and _token_hash(token) in rows


def test_logout_kills_the_session():
    c = signed_in("casey")
    token = c.cookies["fm_session"]
    c.post("/api/auth/logout")
    replay = TestClient(server.app, cookies={"fm_session": token})
    assert replay.get("/api/children").status_code == 401


def test_idle_session_expires():
    c = signed_in("casey")
    old = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    server.store.db.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (old, _token_hash(c.cookies["fm_session"])))
    assert c.get("/api/children").status_code == 401


# ---------- account lifecycle ----------

def test_new_account_must_change_password_first(admin):
    r = admin.post("/api/users", json={"username": "newbie", "full_name": "New Bie", "role": "caseworker",
                                       "temporary_password": "temporary-pass-42"})
    assert r.status_code == 201
    c = TestClient(server.app)
    assert c.post("/api/auth/login", json={"username": "newbie", "password": "temporary-pass-42"}).json()["must_change_password"]
    blocked = c.get("/api/children")
    assert blocked.status_code == 403 and "change your password" in blocked.text
    assert c.post("/api/auth/password", json={"current_password": "temporary-pass-42", "new_password": "short"}).status_code == 400
    assert c.post("/api/auth/password", json={"current_password": "temporary-pass-42",
                                              "new_password": "a-much-better-passphrase"}).status_code == 204
    assert c.get("/api/children").status_code == 200


def test_weak_temporary_password_rejected(admin):
    r = admin.post("/api/users", json={"username": "weak", "full_name": "W", "role": "caseworker", "temporary_password": "123"})
    assert r.status_code == 400


def test_disabling_or_demoting_takes_effect_immediately(admin):
    server.accounts.create("leaver", "Leaver", Role.SUPERVISOR, PASSWORD, must_change=False)
    leaver = signed_in("leaver")
    uid = next(u.id for u in server.accounts.list() if u.username == "leaver")
    assert admin.patch(f"/api/users/{uid}", json={"role": "caseworker"}).status_code == 200
    assert leaver.get("/api/children").status_code == 401  # old session ended on role change
    leaver = signed_in("leaver")
    assert leaver.get("/api/audit/access").status_code == 403  # now a caseworker
    admin.patch(f"/api/users/{uid}", json={"active": False})
    assert leaver.get("/api/children").status_code == 401
    assert TestClient(server.app).post("/api/auth/login", json={"username": "leaver", "password": PASSWORD}).status_code == 401


def test_admin_cannot_lock_themselves_out(admin):
    me = admin.get("/api/auth/me").json()
    assert admin.patch(f"/api/users/{me['id']}", json={"active": False}).status_code == 400
    assert admin.patch(f"/api/users/{me['id']}", json={"role": "caseworker"}).status_code == 400


# ---------- accountability ----------

def test_placement_is_attributed_to_the_signed_in_supervisor(supervisor):
    body = {"applicant": {"first_name": "Al", "last_name": "Home"}, "home": {"available_beds": 1},
            "licensing": {"care_level": 2}, "caseworker": {"application_status": "licensed", "background_checks_complete": True}}
    p = supervisor.post("/api/parents", json=body).json()
    c = supervisor.post("/api/children", json={"demographics": {"first_name": "Bo", "last_name": "Kid",
                                                                 "date_of_birth": "2016-01-01", "gender": "male"}}).json()
    r = supervisor.post("/api/placements", json={"child_id": c["id"], "parent_id": p["id"], "decided_by": "Somebody Else"})
    assert r.status_code == 201 and r.json()["decided_by"] == "Sam (sam)"


def test_record_views_are_logged_with_the_user(caseworker):
    cid = child_id()
    caseworker.get(f"/api/children/{cid}")
    assert any(e["action"] == f"GET /api/children/{cid}" and e["username"] == "casey" for e in server.accounts.recent_log(10))


def test_cross_origin_writes_are_refused(supervisor):
    r = supervisor.post("/api/children", json={}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_concurrent_requests_do_not_corrupt_the_database(supervisor):
    """Regression: parallel requests on one shared SQLite connection used to fail with InterfaceError."""
    from concurrent.futures import ThreadPoolExecutor

    paths = ["/api/children", "/api/parents", "/api/placements", "/api/match/board", "/api/audit/access", "/api/ai/usage"] * 8
    with ThreadPoolExecutor(max_workers=12) as pool:
        codes = list(pool.map(lambda p: supervisor.get(p).status_code, paths))
    assert set(codes) == {200}


def test_a_child_cannot_be_placed_twice(supervisor):
    home = {"applicant": {"first_name": "Twice", "last_name": "Home"}, "home": {"available_beds": 3},
            "preferences": {"max_children": 3}, "licensing": {"care_level": 2},
            "caseworker": {"application_status": "licensed", "background_checks_complete": True}}
    a = supervisor.post("/api/parents", json=home).json()
    b = supervisor.post("/api/parents", json=home).json()
    kid = supervisor.post("/api/children", json={"demographics": {"first_name": "Once", "last_name": "Only",
                                                                   "date_of_birth": "2015-05-05", "gender": "female"}}).json()
    assert supervisor.post("/api/placements", json={"child_id": kid["id"], "parent_id": a["id"]}).status_code == 201
    assert supervisor.post("/api/placements", json={"child_id": kid["id"], "parent_id": b["id"]}).status_code == 409
