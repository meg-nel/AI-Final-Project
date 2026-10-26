"""Caseload limits (caseworkers see only their own records) and review-mode sign-in."""

import pytest
from fastapi.testclient import TestClient

from backend import agent, auth
from backend import main as server
from tests.conftest import signed_in


@pytest.fixture
def meg():
    return signed_in("meg")  # owns the demo caseload


@pytest.fixture
def casey():
    return signed_in("casey")  # a second caseworker


def child_named(first):
    return next(c for c in server.store.list_children() if c.demographics.first_name == first)


def new_child(client, first, assigned=None):
    body = {"demographics": {"first_name": first, "last_name": "Case", "date_of_birth": "2016-04-04", "gender": "female"}}
    if assigned is not None:
        body["caseworker"] = {"assigned_caseworker": assigned}
    return client.post("/api/children", json=body).json()


def test_caseworker_lists_only_their_own_records(meg, casey):
    mine = {c["id"] for c in meg.get("/api/children").json()}
    assert mine == {c.id for c in server.store.list_children() if c.caseworker.assigned_caseworker == "meg"} and mine
    assert {p["id"] for p in meg.get("/api/parents").json()} == \
           {p.id for p in server.store.list_parents() if p.caseworker.assigned_caseworker == "meg"}
    assert not {c["id"] for c in casey.get("/api/children").json()} & mine


def test_supervisor_sees_every_record(supervisor):
    assert len(supervisor.get("/api/children").json()) == len(server.store.list_children())
    assert len(supervisor.get("/api/parents").json()) == len(server.store.list_parents())


def test_other_caseworkers_records_are_refused(casey, monkeypatch):
    monkeypatch.setattr(agent, "run_match", lambda *_: pytest.fail("must not reach the model"))
    kai = child_named("Kai")  # Meg's case
    rosa = next(p for p in server.store.list_parents() if p.applicant.first_name == "Rosa")
    for path in (f"/api/children/{kai.id}", f"/api/parents/{rosa.id}", f"/api/match/child/{kai.id}",
                 f"/api/match/pair/{rosa.id}/{kai.id}", f"/api/ai/match/{kai.id}"):
        assert casey.get(path).status_code == 403, path
    assert casey.post(f"/api/ai/match/{kai.id}").status_code == 403
    assert casey.post(f"/api/ai/summary/child/{kai.id}").status_code == 403
    assert casey.put(f"/api/children/{kai.id}", json=kai.model_dump(mode="json")).status_code == 403
    assert any(e["action"] == "caseload denied" and e["username"] == "casey" for e in server.accounts.recent_log(20))


def test_caseworker_can_match_own_child_against_all_homes(meg):
    jordan = child_named("Jordan")
    results = meg.get(f"/api/match/child/{jordan.id}").json()
    assert len(results) == len(server.store.list_parents())  # every home's rules result, for placement


def test_board_shows_only_own_children(meg, casey):
    theirs = new_child(casey, "Boardy")
    mine = {r["child_id"] for r in meg.get("/api/match/board").json()}
    casey_rows = {r["child_id"] for r in casey.get("/api/match/board").json()}
    assert theirs["id"] in casey_rows and theirs["id"] not in mine and not mine & casey_rows


def test_new_records_are_filed_to_the_caseworker_and_cannot_be_reassigned(meg, casey):
    created = new_child(casey, "Filed", assigned="meg")
    assert created["caseworker"]["assigned_caseworker"] == "casey"  # filed to the creator, not the requested worker
    created["caseworker"]["assigned_caseworker"] = "meg"
    assert casey.put(f"/api/children/{created['id']}", json=created).json()["caseworker"]["assigned_caseworker"] == "casey"
    assert meg.get(f"/api/children/{created['id']}").status_code == 403


def test_supervisor_reassigns_cases(supervisor, meg):
    child = new_child(supervisor, "Moved", assigned="")  # unassigned: only supervisors see it
    assert meg.get(f"/api/children/{child['id']}").status_code == 403
    child["caseworker"]["assigned_caseworker"] = "meg"
    assert supervisor.put(f"/api/children/{child['id']}", json=child).status_code == 200
    assert meg.get(f"/api/children/{child['id']}").status_code == 200
    child["caseworker"]["assigned_caseworker"] = "not-a-user"
    assert supervisor.put(f"/api/children/{child['id']}", json=child).status_code == 400


def test_placement_log_is_scoped(casey, supervisor):
    assert all(server.store.get_child(p["child_id"]).caseworker.assigned_caseworker == "casey"
               for p in casey.get("/api/placements").json())
    assert len(supervisor.get("/api/placements").json()) == len(server.store.list_placements())


def test_staff_directory_lists_assignable_people(meg):
    staff = {s["username"]: s["role"] for s in meg.get("/api/staff").json()}
    assert staff["meg"] == "caseworker" and staff["sam"] == "supervisor" and "ada" not in staff


def test_legacy_free_text_assignments_are_linked():
    child = child_named("Mateo")
    child.caseworker.assigned_caseworker = "Meg"  # a typed name from an older version
    server.store.save_child(server.FosterChildIntake.model_validate(child.model_dump()), child.id)
    server._link_legacy_assignments()
    assert server.store.get_child(child.id).caseworker.assigned_caseworker == "meg"


# ---------- review mode ----------

@pytest.fixture
def review_mode(monkeypatch):
    monkeypatch.setattr(auth, "DEMO_SIGNIN", True)
    auth.ensure_demo_accounts(server.accounts)


def test_review_mode_offers_meg_tauhid_and_admin(review_mode):
    cfg = TestClient(server.app).get("/api/auth/config").json()
    assert cfg["review_mode"]
    assert {(a["username"], a["full_name"], a["role"]) for a in cfg["accounts"]} == {
        ("meg", "Meg", "caseworker"), ("tauhid", "Tauhid", "supervisor"), ("admin", "System Administrator", "admin")}


def test_review_mode_signs_in_without_password(review_mode):
    c = TestClient(server.app)
    r = c.post("/api/auth/login", json={"username": "tauhid"})
    assert r.status_code == 200 and r.json()["review_mode"] and not r.json()["must_change_password"]
    assert len(c.get("/api/children").json()) == len(server.store.list_children())  # supervisor sees all
    assert any(e["action"] == "login (review mode, no password)" for e in server.accounts.recent_log(5))


def test_review_mode_still_enforces_roles_and_caseloads(review_mode, supervisor):
    hidden = new_child(supervisor, "Hidden", assigned="")
    c = TestClient(server.app)
    c.post("/api/auth/login", json={"username": "meg"})
    assert c.post("/api/placements", json={"child_id": "x", "parent_id": "y"}).status_code == 403
    assert c.get(f"/api/children/{hidden['id']}").status_code == 403
    assert c.get("/api/users").status_code == 403
    assert TestClient(server.app).get("/api/children").status_code == 401  # still needs a session
    assert TestClient(server.app).post("/api/auth/login", json={"username": "nobody"}).status_code == 401


def test_password_mode_hides_demo_accounts_and_requires_password():
    assert auth.DEMO_SIGNIN is False
    c = TestClient(server.app)
    assert c.get("/api/auth/config").json() == {"review_mode": False, "accounts": []}
    assert c.post("/api/auth/login", json={"username": "meg"}).status_code == 401
