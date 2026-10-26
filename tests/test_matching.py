"""Rules engine, storage, and API tests. No AI calls: these run offline."""

import os

os.environ["FOSTERMATCH_DB"] = ":memory:"

import pytest
from fastapi.testclient import TestClient

from backend.agent import child_view, parent_view
from backend.main import app, store
from backend.models import FosterChildIntake, FosterParentIntake
from backend.scoring import evaluate, placement_board

client = TestClient(app)


def by_name(records, first):
    return next(r for r in records if r.applicant.first_name == first) if hasattr(records[0], "applicant") \
        else next(r for r in records if r.demographics.first_name == first)


@pytest.fixture
def data():
    return store.list_parents(), store.list_children()


def test_kinship_home_only_serves_related_child(data):
    parents, children = data
    rosa = by_name(parents, "Rosa")
    assert evaluate(rosa, by_name(children, "Mateo")).eligible
    result = evaluate(rosa, by_name(children, "Kai"))
    assert not result.eligible
    assert any("kinship" in b.lower() for b in result.blockers)


def test_care_level_and_application_status_are_hard_rules(data):
    parents, children = data
    jordan = by_name(children, "Jordan")  # needs Level 4
    assert not evaluate(by_name(parents, "Dana"), jordan).eligible
    assert evaluate(by_name(parents, "Priya"), jordan).eligible
    parks = evaluate(by_name(parents, "James"), by_name(children, "Ava"))
    assert any("in_review" in b for b in parks.blockers)


def test_medical_level_does_not_cover_therapeutic(data):
    parents, children = data
    assert not evaluate(by_name(parents, "Grace"), by_name(children, "Jordan")).eligible


def test_sibling_group_needs_beds_and_willingness(data):
    parents, children = data
    ava = by_name(children, "Ava")
    assert ava.sibling_group_size == 2
    assert evaluate(by_name(parents, "Dana"), ava).eligible
    assert not evaluate(by_name(parents, "Grace"), ava).eligible


def test_pet_allergy_blocks_home_with_pets(data):
    parents, children = data
    sofia = by_name(children, "Sofia")
    theo = by_name(parents, "Theo")
    assert any("pet allergy" in b for b in evaluate(theo, sofia).blockers)


def test_race_never_changes_the_score(data):
    parents, children = data
    mateo = by_name(children, "Mateo")
    changed = mateo.model_copy(deep=True)
    changed.demographics.race_ethnicity = "Something else entirely"
    for p in parents:
        assert evaluate(p, mateo).score == evaluate(p, changed).score


def test_shared_surname_alone_is_not_kinship(data):
    parents, children = data
    kai = by_name(children, "Kai").model_copy(deep=True)
    kai.goals.known_relatives = ["Maria Delgado (family friend)"]
    assert not evaluate(by_name(parents, "Rosa"), kai).eligible


def test_board_keeps_siblings_together_and_respects_beds(data):
    parents, children = data
    rows = {r.child_name: r for r in placement_board(children, parents)}
    assert rows["Ava Thompson"].proposed.parent_id == rows["Liam Thompson"].proposed.parent_id
    assert rows["Mateo Ruiz"].proposed.parent_name == "Rosa Delgado"
    # Priya & Sam have one bed: only one child can be proposed there
    priya = by_name(parents, "Priya").id
    assert sum(1 for r in rows.values() if r.proposed and r.proposed.parent_id == priya) == 1


def test_ai_views_redact_contact_details_and_race(data):
    parents, children = data
    pv = parent_view(by_name(parents, "Dana"))
    assert "phone" not in pv["applicant"] and "street" not in pv["home"]["address"]
    cv = child_view(by_name(children, "Mateo"))
    assert "race_ethnicity" not in cv["demographics"] and "date_of_birth" not in cv["demographics"]


def test_api_create_update_and_place():
    parent = {
        "applicant": {"first_name": "Ana", "last_name": "Test"},
        "home": {"available_beds": 1, "school_district": "Harbor SD", "address": {"county": "Harbor"}},
        "licensing": {"care_level": 2},
        "preferences": {"age_min": 5, "age_max": 15},
        "caseworker": {"application_status": "licensed", "background_checks_complete": True},
    }
    child = {
        "demographics": {"first_name": "Bo", "last_name": "Test", "date_of_birth": "2016-01-01", "gender": "male"},
        "education": {"school_district": "Harbor SD"},
    }
    p = client.post("/api/parents", json=parent).json()
    c = client.post("/api/children", json=child).json()
    assert p["id"].startswith("P-") and c["age"] >= 10

    parent["applicant"]["occupation"] = "Nurse"
    assert client.put(f"/api/parents/{p['id']}", json=parent).json()["applicant"]["occupation"] == "Nurse"

    pair = client.get(f"/api/match/pair/{p['id']}/{c['id']}").json()
    assert pair["eligible"] and pair["score"] > 0

    r = client.post("/api/placements", json={"child_id": c["id"], "parent_id": p["id"], "decided_by": "Tester"})
    assert r.status_code == 201
    assert client.get(f"/api/children/{c['id']}").json()["caseworker"]["placement_status"] == "placed"
    assert client.get(f"/api/parents/{p['id']}").json()["home"]["available_beds"] == 0

    # home is now full: a second placement is refused
    c2 = client.post("/api/children", json=child).json()
    assert client.post("/api/placements", json={"child_id": c2["id"], "parent_id": p["id"]}).status_code == 409


def test_invalid_intake_is_rejected():
    r = client.post("/api/parents", json={"applicant": {"first_name": "X"}, "licensing": {"care_level": 9}})
    assert r.status_code == 422


def test_intake_models_round_trip(data):
    parents, children = data
    FosterParentIntake.model_validate(parents[0].model_dump())
    FosterChildIntake.model_validate(children[0].model_dump())
