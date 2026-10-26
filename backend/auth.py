"""Staff accounts, sessions, and role-based access control.

Roles (least privilege):
* caseworker: read and edit intake records, run rules-based matching and the AI agents.
* supervisor: everything a caseworker can do, plus approve placements, delete records, and read the audit logs.
* admin: manage staff accounts and read the audit logs. No access to case records (separation of duties).

Review mode (FOSTERMATCH_DEMO_SIGNIN=1, the current default): the sign-in page offers one-click demo accounts and
no password is checked, so the project can be reviewed without credentials. Roles, caseload limits, sessions, and
the access log all still apply. Set FOSTERMATCH_DEMO_SIGNIN=0 to require passwords. Every password protection below
then applies unchanged.

Sessions are random tokens in an HttpOnly, SameSite=Strict cookie. Only a SHA-256 of each token is stored, so a copy
of the database can't be used to hijack sessions. Sessions expire after 30 minutes idle or 8 hours total. Passwords
are hashed with scrypt (Python stdlib). Five failed logins lock an account for 15 minutes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

log = logging.getLogger("fostermatch.auth")

COOKIE = "fm_session"
IDLE_TIMEOUT = timedelta(minutes=30)
ABSOLUTE_TIMEOUT = timedelta(hours=8)
MAX_FAILED_LOGINS = 5
LOCKOUT = timedelta(minutes=15)
MIN_PASSWORD_LENGTH = 12
SECURE_COOKIES = (os.getenv("FOSTERMATCH_SECURE_COOKIES") or "0") == "1"  # set to 1 when served over HTTPS
DEMO_SIGNIN = (os.getenv("FOSTERMATCH_DEMO_SIGNIN") or "1") == "1"  # review mode: passwordless demo sign-in

# Demo staff for review mode. The demo caseload (seed.py) is assigned to Meg.
DEMO_ACCOUNTS = [
    ("meg", "Meg", "caseworker"),
    ("tauhid", "Tauhid", "supervisor"),
    ("admin", "System Administrator", "admin"),
]


class Role(str, Enum):
    CASEWORKER = "caseworker"
    SUPERVISOR = "supervisor"
    ADMIN = "admin"


PERMISSIONS: dict[str, set[Role]] = {
    "records:read": {Role.CASEWORKER, Role.SUPERVISOR},
    "records:write": {Role.CASEWORKER, Role.SUPERVISOR},
    "records:delete": {Role.SUPERVISOR},
    "ai:run": {Role.CASEWORKER, Role.SUPERVISOR},
    "placements:approve": {Role.SUPERVISOR},
    "audit:read": {Role.SUPERVISOR, Role.ADMIN},
    "users:manage": {Role.ADMIN},
}


def permissions_for(role: Role) -> list[str]:
    return sorted(p for p, roles in PERMISSIONS.items() if role in roles)


# ---------- password hashing (scrypt, stdlib) ----------

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        candidate = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(candidate, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))  # equalises timing for unknown usernames


def password_problem(password: str, username: str = "") -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if username and username.lower() in password.lower():
        return "Password must not contain the username."
    if len(set(password)) < 6:
        return "Password is too repetitive."
    return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- models ----------

class User(BaseModel):
    id: int
    username: str
    full_name: str
    role: Role
    active: bool
    must_change_password: bool
    last_login: str | None = None

    @property
    def permissions(self) -> list[str]:
        return permissions_for(self.role)


class LoginRequest(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(default="", max_length=256)  # ignored in review mode


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class NewUser(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    full_name: str = Field(min_length=1, max_length=120)
    role: Role
    temporary_password: str = Field(max_length=256)


class UserUpdate(BaseModel):
    role: Role | None = None
    active: bool | None = None
    temporary_password: str | None = Field(default=None, max_length=256)


# ---------- persistence (same SQLite database as the case records) ----------

class Accounts:
    def __init__(self, db):
        self.db = db
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE COLLATE NOCASE NOT NULL,
                full_name TEXT NOT NULL, role TEXT NOT NULL, password_hash TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1, must_change_password INTEGER NOT NULL DEFAULT 1,
                failed_logins INTEGER NOT NULL DEFAULT 0, locked_until TEXT, last_login TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at TEXT NOT NULL, last_seen TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS access_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, username TEXT, role TEXT, action TEXT NOT NULL,
                status INTEGER, ip TEXT, detail TEXT);
            """
        )

    _COLS = "id, username, full_name, role, active, must_change_password, last_login"

    def _user(self, row) -> User | None:
        if not row:
            return None
        return User(id=row[0], username=row[1], full_name=row[2], role=row[3], active=bool(row[4]),
                    must_change_password=bool(row[5]), last_login=row[6])

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def by_username(self, username: str) -> User | None:
        return self._user(self.db.execute(f"SELECT {self._COLS} FROM users WHERE username = ?", (username,)).fetchone())

    def get(self, user_id: int) -> User | None:
        return self._user(self.db.execute(f"SELECT {self._COLS} FROM users WHERE id = ?", (user_id,)).fetchone())

    def list(self) -> list[User]:
        return [self._user(r) for r in self.db.execute(f"SELECT {self._COLS} FROM users ORDER BY username").fetchall()]

    def create(self, username: str, full_name: str, role: Role, password: str, must_change: bool = True) -> User:
        try:
            cur = self.db.execute(
                "INSERT INTO users (username, full_name, role, password_hash, must_change_password, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (username, full_name, Role(role).value, hash_password(password), int(must_change), _now().isoformat()),
            )
        except Exception as e:
            if "UNIQUE" in str(e):
                raise ValueError("That username is taken.") from None
            raise
        self.db.commit()
        return self.get(cur.lastrowid)

    def set_password(self, user_id: int, password: str, must_change: bool) -> None:
        self.db.execute(
            "UPDATE users SET password_hash = ?, must_change_password = ?, failed_logins = 0, locked_until = NULL WHERE id = ?",
            (hash_password(password), int(must_change), user_id),
        )
        self.db.commit()

    def update(self, user_id: int, role: Role | None = None, active: bool | None = None) -> None:
        if role is not None:
            self.db.execute("UPDATE users SET role = ? WHERE id = ?", (Role(role).value, user_id))
        if active is not None:
            self.db.execute("UPDATE users SET active = ? WHERE id = ?", (int(active), user_id))
        self.db.commit()

    # ---------- login ----------

    def authenticate(self, username: str, password: str) -> tuple[User | None, str]:
        """Returns (user, "") on success or (None, reason) on failure. The reason is for the access log only;
        the caller always shows the same generic message."""
        row = self.db.execute(
            "SELECT id, password_hash, active, failed_logins, locked_until FROM users WHERE username = ?", (username,)
        ).fetchone()
        if not row:
            verify_password(password, _DUMMY_HASH)
            return None, "unknown user"
        user_id, stored, active, failed, locked_until = row
        if locked_until and datetime.fromisoformat(locked_until) > _now():
            return None, "locked"
        if not verify_password(password, stored):
            failed += 1
            lock = (_now() + LOCKOUT).isoformat() if failed >= MAX_FAILED_LOGINS else None
            self.db.execute("UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?",
                            (0 if lock else failed, lock, user_id))
            self.db.commit()
            return None, "locked after repeated failures" if lock else "bad password"
        if not active:
            return None, "disabled"
        self.db.execute("UPDATE users SET failed_logins = 0, locked_until = NULL, last_login = ? WHERE id = ?",
                        (_now().isoformat(), user_id))
        self.db.commit()
        return self.get(user_id), ""

    # ---------- sessions ----------

    def start_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        now = _now().isoformat()
        self.db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (_token_hash(token), user_id, now, now))
        self.db.commit()
        return token

    def session_user(self, token: str) -> User | None:
        th = _token_hash(token)
        row = self.db.execute("SELECT user_id, created_at, last_seen FROM sessions WHERE token_hash = ?", (th,)).fetchone()
        if not row:
            return None
        now = _now()
        if now - datetime.fromisoformat(row[2]) > IDLE_TIMEOUT or now - datetime.fromisoformat(row[1]) > ABSOLUTE_TIMEOUT:
            self.end_session(token)
            return None
        user = self.get(row[0])
        if not user or not user.active:
            self.end_session(token)
            return None
        self.db.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now.isoformat(), th))
        self.db.commit()
        return user

    def end_session(self, token: str) -> None:
        self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
        self.db.commit()

    def end_all_sessions(self, user_id: int, keep: str | None = None) -> None:
        self.db.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                        (user_id, _token_hash(keep) if keep else ""))
        self.db.commit()

    # ---------- access log ----------

    def log(self, action: str, user: User | None = None, username: str | None = None, status: int | None = None,
            ip: str | None = None, detail: str = "") -> None:
        self.db.execute(
            "INSERT INTO access_log (at, username, role, action, status, ip, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_now().isoformat(), user.username if user else username, user.role.value if user else None,
             action, status, ip, detail[:300]),
        )
        self.db.commit()

    def recent_log(self, limit: int = 200) -> list[dict]:
        rows = self.db.execute(
            "SELECT at, username, role, action, status, ip, detail FROM access_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(zip(("at", "username", "role", "action", "status", "ip", "detail"), r)) for r in rows]


def ensure_demo_accounts(accounts: Accounts) -> None:
    """Review mode: make sure the demo staff exist. Their passwords are random and never shown: in review mode no
    password is checked, and if password sign-in is switched on later, an admin resets them (they must change it)."""
    for username, full_name, role in DEMO_ACCOUNTS:
        if not accounts.by_username(username):
            accounts.create(username, full_name, Role(role), secrets.token_urlsafe(16), must_change=True)


def bootstrap_admin(accounts: Accounts) -> None:
    """First run: create an 'admin' account so someone can sign in and create staff accounts.

    The password comes from FOSTERMATCH_ADMIN_PASSWORD, or is generated and written to the server log once. Either
    way it must be changed at first sign-in.
    """
    if accounts.count():
        return
    password = os.getenv("FOSTERMATCH_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
    accounts.create("admin", "System Administrator", Role.ADMIN, password, must_change=True)
    if not os.getenv("FOSTERMATCH_ADMIN_PASSWORD"):
        log.warning("Created first account 'admin' with one-time password: %s (you must change it at first sign-in)", password)
        print(f"\n*** First run: sign in as 'admin' with one-time password {password} and change it. ***\n", flush=True)


# ---------- FastAPI wiring ----------

def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def build_router(accounts: Accounts) -> tuple[APIRouter, callable]:
    """Returns the auth/users router and a `require(permission)` dependency factory."""
    router = APIRouter()

    def current_user(request: Request) -> User:
        token = request.cookies.get(COOKIE)
        user = accounts.session_user(token) if token else None
        if not user:
            raise HTTPException(401, "Please sign in.")
        request.state.user = user
        return user

    def require(permission: str | None = None):
        if permission is not None and permission not in PERMISSIONS:
            raise ValueError(f"Unknown permission {permission}")

        def dependency(request: Request, user: User = Depends(current_user)) -> User:
            if user.must_change_password and not DEMO_SIGNIN:
                raise HTTPException(403, "You must change your password before continuing.")
            if permission and user.role not in PERMISSIONS[permission]:
                accounts.log("permission denied", user, status=403, ip=client_ip(request),
                             detail=f"{request.method} {request.url.path} needs {permission}")
                raise HTTPException(403, "Your role does not allow this action.")
            return user

        return dependency

    def set_cookie(response: Response, token: str) -> None:
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=SECURE_COOKIES,
                            max_age=int(ABSOLUTE_TIMEOUT.total_seconds()), path="/")

    def me(user: User) -> dict:
        data = {**user.model_dump(), "permissions": user.permissions, "review_mode": DEMO_SIGNIN}
        if DEMO_SIGNIN:
            data["must_change_password"] = False
        return data

    @router.get("/api/auth/config")
    def config() -> dict:
        """Public: tells the sign-in page whether review mode is on. Demo accounts are listed only in review mode."""
        if not DEMO_SIGNIN:
            return {"review_mode": False, "accounts": []}
        listed = [accounts.by_username(u) for u, _, _ in DEMO_ACCOUNTS]
        return {"review_mode": True, "accounts": [
            {"username": u.username, "full_name": u.full_name, "role": u.role.value} for u in listed if u and u.active]}

    @router.post("/api/auth/login")
    def login(body: LoginRequest, request: Request, response: Response) -> dict:
        if DEMO_SIGNIN:  # review mode: no password check (accounts must still exist and be active)
            user = accounts.by_username(body.username.strip())
            user = user if user and user.active else None
            reason = "unknown or disabled account"
        else:
            user, reason = accounts.authenticate(body.username.strip(), body.password)
        if not user:
            accounts.log("login failed", username=body.username.strip()[:64], status=401, ip=client_ip(request), detail=reason)
            raise HTTPException(401, "Invalid username or password, or the account is locked.")
        set_cookie(response, accounts.start_session(user.id))
        accounts.log("login (review mode, no password)" if DEMO_SIGNIN else "login", user, status=200, ip=client_ip(request))
        return me(user)

    @router.post("/api/auth/logout", status_code=204)
    def logout(request: Request, response: Response) -> None:
        token = request.cookies.get(COOKIE)
        if token:
            user = accounts.session_user(token)
            accounts.end_session(token)
            if user:
                accounts.log("logout", user, status=204, ip=client_ip(request))
        response.delete_cookie(COOKIE, path="/")

    @router.get("/api/auth/me")
    def whoami(user: User = Depends(current_user)) -> dict:
        return me(user)

    @router.post("/api/auth/password", status_code=204)
    def change_password(body: PasswordChange, request: Request, user: User = Depends(current_user)) -> None:
        row = accounts.db.execute("SELECT password_hash FROM users WHERE id = ?", (user.id,)).fetchone()
        if not verify_password(body.current_password, row[0]):
            accounts.log("password change failed", user, status=400, ip=client_ip(request))
            raise HTTPException(400, "Current password is incorrect.")
        if body.new_password == body.current_password:
            raise HTTPException(400, "Choose a new password that differs from the current one.")
        if problem := password_problem(body.new_password, user.username):
            raise HTTPException(400, problem)
        accounts.set_password(user.id, body.new_password, must_change=False)
        accounts.end_all_sessions(user.id, keep=request.cookies.get(COOKIE))  # sign out other devices
        accounts.log("password changed", user, status=204, ip=client_ip(request))

    # ---------- user administration (admin only) ----------

    @router.get("/api/users")
    def list_users(user: User = Depends(require("users:manage"))) -> list[User]:
        return accounts.list()

    @router.post("/api/users", status_code=201)
    def create_user(body: NewUser, request: Request, user: User = Depends(require("users:manage"))) -> User:
        if problem := password_problem(body.temporary_password, body.username):
            raise HTTPException(400, problem)
        try:
            created = accounts.create(body.username, body.full_name, body.role, body.temporary_password, must_change=True)
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        accounts.log("user created", user, status=201, ip=client_ip(request), detail=f"{created.username} as {created.role.value}")
        return created

    @router.patch("/api/users/{user_id}")
    def update_user(user_id: int, body: UserUpdate, request: Request, user: User = Depends(require("users:manage"))) -> User:
        target = accounts.get(user_id)
        if not target:
            raise HTTPException(404, "No such user.")
        if target.id == user.id and (body.active is False or (body.role and body.role != Role.ADMIN)):
            raise HTTPException(400, "You can't disable or demote your own account.")
        if body.temporary_password is not None:
            if problem := password_problem(body.temporary_password, target.username):
                raise HTTPException(400, problem)
            accounts.set_password(user_id, body.temporary_password, must_change=True)
        accounts.update(user_id, role=body.role, active=body.active)
        if body.active is False or body.role is not None or body.temporary_password is not None:
            accounts.end_all_sessions(user_id)  # changes take effect immediately
        changes = ", ".join(f"{k}={v.value if isinstance(v, Enum) else ('***' if k == 'temporary_password' else v)}"
                            for k, v in body.model_dump(exclude_none=True).items())
        accounts.log("user updated", user, status=200, ip=client_ip(request), detail=f"{target.username}: {changes}")
        return accounts.get(user_id)

    @router.get("/api/audit/access")
    def access_log(user: User = Depends(require("audit:read"))) -> list[dict]:
        return accounts.recent_log()

    return router, require
