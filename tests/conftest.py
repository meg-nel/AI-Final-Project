import os

os.environ["FOSTERMATCH_DB"] = ":memory:"
os.environ["FOSTERMATCH_ADMIN_PASSWORD"] = "bootstrap-admin-pass-1"
os.environ["PYDANTIC_AI_NO_BANNER"] = "1"
os.environ["FOSTERMATCH_DEMO_SIGNIN"] = "0"  # test the real password protection; review mode has its own tests

import pytest
from fastapi.testclient import TestClient

from backend import main as server
from backend.auth import Role

PASSWORD = "correct-horse-battery-9"
# meg owns the demo caseload (seed.py); casey is a second caseworker with no cases, to test caseload limits.
STAFF = {"meg": Role.CASEWORKER, "casey": Role.CASEWORKER, "sam": Role.SUPERVISOR, "ada": Role.ADMIN}

for username, role in STAFF.items():
    if not any(u.username == username for u in server.accounts.list()):
        server.accounts.create(username, username.title(), role, PASSWORD, must_change=False)


def signed_in(username: str) -> TestClient:
    client = TestClient(server.app)
    r = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture
def caseworker() -> TestClient:
    return signed_in("casey")


@pytest.fixture
def supervisor() -> TestClient:
    return signed_in("sam")


@pytest.fixture
def admin() -> TestClient:
    return signed_in("ada")
