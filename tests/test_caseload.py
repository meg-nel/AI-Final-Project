"""Caseload limits (caseworkers see only their own records) and review-mode sign-in."""

import pytest
from fastapi.testclient import TestClient

from backend import agent, auth
from backend import main as server
from tests.conftest import signed_in


def ids(records, who):
    return {r.id for r in records if r.caseworker.assigned_caseworker == who}


@pytest.fixture
def osei():
    return signed_in("kosei")


@pytest.fixture
def varga():
    return signed_in("lvarga")


def child_named(first):
    return next(c for c in server.store.list_children() if c.demographics.first_name == first)


def test_caseworker_lists_only_their_own_records(osei):
    children = {c["id"] for c in osei.get("/api/children").json()}
    homes = {p["id"] for p in osei.get("/api/parents").json()}
    assert children == ids(server.store.list_children(), "kosei") and children
    assert homes == ids(server.store.list_parents(), "kosei") and homes


def test_supervisor_sees_every_record(supervisor):
    assert len(supervisor.get("/api/children").json()) == len(server.store.list_children())
    assert len(supervisor.get("/api/parents").json()) == len(server.store.list_parents())


def test_other_caseworkers_records_are_refused(osei, monkeypatch):
    monkeypatch.setattr(agent, "run_match", lambda *_: pytest.fail("must not reach the model"))
    kai = child_named("Kai")  # Lena Varga's case
    rosa = next(p for p in server.store.list_parents() if p.applicant.first_name == "Rosa")
    for path in (f"/api/children/{kai.id}", f"/api/parents/{rosa.id}", f"/api/match/child/{kai.id}",
                 f"/api/match/pair/{rosa.id}/{kai.id}", f"/api/ai/match/{kai.id}"):
        assert osei.get(path).status_code == 403, path
    assert osei.post(f"/api/ai/match/{kai.id}").status_code == 403
    assert osei.post(f"/api/ai/summary/child/{kai.id}").status_code == 403
    assert osei.put(f"/api/children/{kai.id}", json=kai.model_dump(mode="json")).status_code == 403
    assert any(e["action"] == "caseload denied" and e["username"] == "kosei" for e in server.accounts.recent_log(20))


def test_caseworker_can_match_own_child_against_all_homes(osei):
    jordan = child_named("Jordan")  # Kwame Osei's case
    results = osei.get(f"/api/match/child/{jordan.id}").json()
    assert len(results) == len(server.store.list_parents())  # every home's rules result, for placement
    assert osei.get(f"/api/children/{jordan.id}").status_code == 200


def test_board_shows_only_own_children(osei, varga):
    mine = {r["child_id"] for r in osei.get("/api/match/board").json()}
    theirs = {r["child_id"] for r in varga.get("/api/match/board").json()}
    assert mine and theirs and not mine & theirs


def test_new_records_are_filed_to_the_caseworker_and_cannot_be_reassigned(osei, varga):
    body = {"demographics": {"first_name": "New", "last_name": "Case", "date_of_birth": "2018-02-02", "gender": "female"},
            "caseworker": {"assigned_caseworker": "lvarga"}}
    created = osei.post("/api/children", json=body).json()
    assert created["caseworker"]["assigned_caseworker"] == "kosei"  # filed to the creator, not the requested worker
    created["caseworker"]["assigned_caseworker"] = "lvarga"
    updated = osei.put(f"/api/children/{created['id']}", json=created).json()
    assert updated["caseworker"]["assigned_caseworker"] == "kosei"  # reassignment ignored
    assert varga.get(f"/api/children/{created['id']}").status_code == 403


def test_supervisor_reassigns_cases(supervisor, varga):
    child = supervisor.post("/api/children", json={"demographics": {"first_name": "Moved", "last_name": "Case",
                                                                     "date_of_birth": "2014-03-03", "gender": "male"}}).json()
    assert varga.get(f"/api/children/{child['id']}").status_code == 403
    child["caseworker"]["assigned_caseworker"] = "lvarga"
    assert supervisor.put(f"/api/children/{child['id']}", json=child).status_code == 200
    assert varga.get(f"/api/children/{child['id']}").status_code == 200
    child["caseworker"]["assigned_caseworker"] = "not-a-user"
    assert supervisor.put(f"/api/children/{child['id']}", json=child).status_code == 400


def test_placement_log_is_scoped(osei, supervisor):
    for p in osei.get("/api/placements").json():
        assert server.store.get_child(p["child_id"]).caseworker.assigned_caseworker == "kosei"
    assert len(supervisor.get("/api/placements").json()) == len(server.store.list_placements())


def test_staff_directory_lists_assignable_people(osei):
    staff = {s["username"]: s["role"] for s in osei.get("/api/staff").json()}
    assert staff["kosei"] == "caseworker" and staff["sam"] == "supervisor" and "ada" not in staff


def test_legacy_free_text_assignments_are_linked():
    child = child_named("Mateo")
    child.caseworker.assigned_caseworker = "K. Osei"
    server.store.save_child(server.FosterChildIntake.model_validate(child.model_dump()), child.id)
    server.accounts.db.execute("UPDATE users SET full_name = 'Kwame Osei' WHERE username = 'kosei'")
    server._link_legacy_assignments()
    assert server.store.get_child(child.id).caseworker.assigned_caseworker == "kosei"
    child = server.store.get_child(child.id)
    child.caseworker.assigned_caseworker = "lvarga"  # restore
    server.store.save_child(server.FosterChildIntake.model_validate(child.model_dump()), child.id)


# ---------- review mode ----------

@pytest.fixture
def review_mode(monkeypatch):
    monkeypatch.setattr(auth, "DEMO_SIGNIN", True)
    auth.ensure_demo_accounts(server.accounts)


def test_review_mode_signs_in_without_password(review_mode):
    c = TestClient(server.app)
    cfg = c.get("/api/auth/config").json()
    assert cfg["review_mode"] and {"kosei", "lvarga", "mchen", "admin"} <= {a["username"] for a in cfg["accounts"]}
    r = c.post("/api/auth/login", json={"username": "mchen"})
    assert r.status_code == 200 and r.json()["review_mode"] and not r.json()["must_change_password"]
    assert c.get("/api/children").status_code == 200
    assert any(e["action"] == "login (review mode, no password)" for e in server.accounts.recent_log(5))


def test_review_mode_still_enforces_roles_and_caseloads(review_mode):
    c = TestClient(server.app)
    c.post("/api/auth/login", json={"username": "kosei"})
    assert c.post("/api/placements", json={"child_id": "x", "parent_id": "y"}).status_code == 403
    assert c.get(f"/api/children/{child_named('Kai').id}").status_code == 403
    assert c.get("/api/users").status_code == 403
    assert TestClient(server.app).get("/api/children").status_code == 401  # still needs a session
    assert TestClient(server.app).post("/api/auth/login", json={"username": "nobody"}).status_code == 401


def test_password_mode_hides_demo_accounts_and_requires_password():
    assert auth.DEMO_SIGNIN is False
    c = TestClient(server.app)
    assert c.get("/api/auth/config").json() == {"review_mode": False, "accounts": []}
    assert c.post("/api/auth/login", json={"username": "kosei"}).status_code == 401
