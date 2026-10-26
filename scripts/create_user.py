"""Create a staff account (or reset a forgotten password) from the command line.

    .venv/Scripts/python scripts/create_user.py USERNAME "Full Name" caseworker|supervisor|admin

Prompts for a temporary password (not echoed). The user must change it at first sign-in. Useful for recovery when
no admin can sign in.
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.auth import Accounts, Role, password_problem  # noqa: E402
from backend.storage import DEFAULT_DB, Store  # noqa: E402


def main() -> None:
    if len(sys.argv) != 4 or sys.argv[3] not in {r.value for r in Role}:
        sys.exit(__doc__)
    username, full_name, role = sys.argv[1:]
    password = getpass.getpass("Temporary password: ")
    if problem := password_problem(password, username):
        sys.exit(problem)
    accounts = Accounts(Store(DEFAULT_DB).db)
    existing = next((u for u in accounts.list() if u.username.lower() == username.lower()), None)
    if existing:
        accounts.set_password(existing.id, password, must_change=True)
        accounts.update(existing.id, role=Role(role), active=True)
        accounts.end_all_sessions(existing.id)
        accounts.log("user reset from command line", username=username, detail=f"role={role}")
        print(f"Reset {username}: role {role}, temporary password set, account enabled.")
    else:
        accounts.create(username, full_name, Role(role), password, must_change=True)
        accounts.log("user created from command line", username=username, detail=f"role={role}")
        print(f"Created {username} ({role}). They must change the password at first sign-in.")


if __name__ == "__main__":
    main()
