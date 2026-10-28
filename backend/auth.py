"""Authentication users and in-memory server-side sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SESSION_COOKIE_NAME = "btf_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
PASSWORD_HASH_ITERATIONS = 600_000
DEVELOPMENT_USERNAME = "Admin@beyondtheframe"
DEVELOPMENT_PASSWORD = "admin@123"
_BOOTSTRAP_ENV_KEYS = {
    "username": ("BTF_AUTH_USERNAME", "BTF_USERNAME", "BTF_ADMIN_USERNAME"),
    "password": ("BTF_AUTH_PASSWORD", "BTF_PASSWORD", "BTF_ADMIN_PASSWORD"),
}
_DUMMY_PASSWORD_HASH = (
    "pbkdf2_sha256$600000$"
    "z85F2j9xw8z5Jt7hQG0FwQ$"
    "tCp0_vH-k6mM1PMpc7S6u1lPX2sW7_Gehq1Srh7CjmI"
)


def _env_value(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def resolve_bootstrap_credentials(
    username: str | None = None,
    password: str | None = None,
) -> tuple[str, str] | None:
    """Resolve explicit bootstrap credentials from arguments or environment.

    The project intentionally does not silently install a default admin account.
    A fresh installation must therefore be bootstrapped using environment/config
    values or an explicit callsite override.
    """
    configured_username = (username if username is not None else _env_value(*_BOOTSTRAP_ENV_KEYS["username"]))
    configured_password = (password if password is not None else _env_value(*_BOOTSTRAP_ENV_KEYS["password"]))

    if configured_username is None and configured_password is None:
        return None
    if not configured_username or not configured_password:
        raise ValueError(
            "Authentication bootstrap requires both username and password. "
            "Set BTF_AUTH_USERNAME and BTF_AUTH_PASSWORD (or BTF_USERNAME/BTF_PASSWORD)."
        )
    return configured_username, configured_password


def legacy_development_credentials_enabled() -> bool:
    return os.environ.get("BTF_ALLOW_DEVELOPMENT_DEFAULTS", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _hash_password(password: str, salt: bytes | None = None) -> str:
    actual_salt = salt or secrets.token_bytes(18)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), actual_salt, PASSWORD_HASH_ITERATIONS
    )
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_HASH_ITERATIONS,
        base64.urlsafe_b64encode(actual_salt).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(derived).decode("ascii").rstrip("="),
    )


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, expected_text = stored_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        if iterations < 100_000 or iterations > 2_000_000:
            return False
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        expected = base64.urlsafe_b64decode(expected_text + "=" * (-len(expected_text) % 4))
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def is_valid_new_password(password: str) -> bool:
    return (
        isinstance(password, str)
        and 8 <= len(password) <= 12
        and any("A" <= character <= "Z" for character in password)
        and any("a" <= character <= "z" for character in password)
        and any("0" <= character <= "9" for character in password)
        and any(not character.isalnum() and not character.isspace() for character in password)
    )


class AuthManager:
    def __init__(
        self,
        database_path: str | Path,
        bootstrap_username: str | None = None,
        bootstrap_password: str | None = None,
    ):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.bootstrap_credentials = resolve_bootstrap_credentials(
            bootstrap_username,
            bootstrap_password,
        )
        if self.bootstrap_credentials is None and legacy_development_credentials_enabled():
            self.bootstrap_credentials = (DEVELOPMENT_USERNAME, DEVELOPMENT_PASSWORD)
        elif self.bootstrap_credentials is None and not self.database_path.exists():
            raise RuntimeError(
                "Authentication is not configured. Set BTF_AUTH_USERNAME and BTF_AUTH_PASSWORD "
                "before starting the backend or pass bootstrap credentials to AuthManager."
            )
        self._sessions: dict[str, tuple[int, str, float]] = {}
        self._session_history: dict[str, dict[str, object]] = {}
        self._lock = threading.RLock()
        self._initialize_users()
        self._close_stale_sessions()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.execute("PRAGMA busy_timeout=15000")
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_users(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    role TEXT NOT NULL DEFAULT 'headquarters_admin',
                    sector_number TEXT,
                    must_change_password INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
                    deactivated_at TEXT
                )
                """
            )
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(users)").fetchall()}
            migrations = {
                "role": "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'headquarters_admin'",
                "sector_number": "ALTER TABLE users ADD COLUMN sector_number TEXT",
                "must_change_password": "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0",
                "is_active": "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))",
                "deactivated_at": "ALTER TABLE users ADD COLUMN deactivated_at TEXT",
            }
            for column, statement in migrations.items():
                if column not in columns:
                    connection.execute(statement)
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_one_sector_account_per_number ON users(sector_number) WHERE role = 'sector'")
            connection.execute("""CREATE TABLE IF NOT EXISTS sector_account_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                sector_number TEXT NOT NULL,
                decommissioned_at TEXT NOT NULL,
                reason TEXT NOT NULL
            )""")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS auth_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            rows = connection.execute("SELECT user_id, username, role FROM users ORDER BY user_id").fetchall()
            if self.bootstrap_credentials is not None:
                username, password = self.bootstrap_credentials
                existing = connection.execute("SELECT user_id, role FROM users WHERE username = ?", (username,)).fetchone()
                hq_admin = connection.execute("SELECT user_id FROM users WHERE role = 'headquarters_admin' LIMIT 1").fetchone()
                if existing is not None:
                    if existing["role"] != "headquarters_admin":
                        raise RuntimeError("The configured bootstrap username belongs to a sector account.")
                    # On first local development bootstrap, repair an existing HQ
                    # account once. Future restarts preserve any password changes.
                    if (username, password) == (DEVELOPMENT_USERNAME, DEVELOPMENT_PASSWORD):
                        initialized = connection.execute(
                            "SELECT 1 FROM auth_metadata WHERE key = 'development_admin_initialized'"
                        ).fetchone()
                        if initialized is None:
                            connection.execute(
                                "UPDATE users SET password_hash = ? WHERE user_id = ?",
                                (_hash_password(DEVELOPMENT_PASSWORD), int(existing["user_id"])),
                            )
                            connection.execute(
                                "INSERT INTO auth_metadata (key, value) VALUES ('development_admin_initialized', '1')"
                            )
                elif hq_admin is not None:
                    raise RuntimeError("Authentication database already contains a different Headquarters administrator.")
                else:
                    connection.execute(
                        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'headquarters_admin')",
                        (username, _hash_password(password)),
                    )
                    if (username, password) == (DEVELOPMENT_USERNAME, DEVELOPMENT_PASSWORD):
                        connection.execute(
                            "INSERT INTO auth_metadata (key, value) VALUES ('development_admin_initialized', '1')"
                        )
                return
            if not rows:
                raise RuntimeError(
                    "Authentication is not configured. Set BTF_AUTH_USERNAME and BTF_AUTH_PASSWORD "
                    "before starting the backend or pass bootstrap credentials to AuthManager."
                )
            if not any(row["role"] == "headquarters_admin" for row in rows):
                raise RuntimeError("No Headquarters administrator is configured. Set BTF_AUTH_USERNAME and BTF_AUTH_PASSWORD.")

    def authenticate(self, username: str, password: str, sector_number: str | None = None, expected_role: str | None = None) -> tuple[int, str] | None:
        if isinstance(username, str) and "@" in username:
            username = username.strip().casefold()
        with self._connection() as connection:
            user = connection.execute(
                "SELECT user_id, username, password_hash, role, sector_number, is_active FROM users WHERE username = ? COLLATE NOCASE",
                (username,),
            ).fetchone()
        stored_hash = user["password_hash"] if user else _DUMMY_PASSWORD_HASH
        password_matches = _verify_password(password, stored_hash)
        if user is None or not password_matches or not bool(user["is_active"]):
            return None
        role = str(user["role"])
        if expected_role is not None and expected_role != role:
            return None
        supplied_sector = sector_number.strip().upper() if isinstance(sector_number, str) else ""
        if role == "sector":
            if not supplied_sector or supplied_sector != str(user["sector_number"] or "").upper():
                return None
        elif role == "headquarters_admin" and supplied_sector:
            return None
        elif role != "headquarters_admin":
            return None
        return int(user["user_id"]), str(user["username"])

    def account_profile(self, user_id: int) -> dict[str, object] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT user_id, username, role, sector_number, must_change_password, is_active, deactivated_at FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "user_id": int(row["user_id"]),
            "username": str(row["username"]),
            "role": str(row["role"]),
            "sector_number": row["sector_number"],
            "must_change_password": bool(row["must_change_password"]),
            "is_active": bool(row["is_active"]),
            "deactivated_at": row["deactivated_at"],
        }

    def list_sector_accounts(self) -> list[dict[str, object]]:
        """Return non-secret shared sector-account metadata for HQ administration."""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT user_id, username, sector_number, created_at, must_change_password "
                "FROM users WHERE role = 'sector' AND is_active = 1 ORDER BY sector_number, user_id"
            ).fetchall()
        return [{"user_id": int(row["user_id"]), "username": str(row["username"]),
                 "sector_number": row["sector_number"], "created_at": row["created_at"],
                 "must_change_password": bool(row["must_change_password"])} for row in rows]

    def headquarters_admin_id(self) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT user_id FROM users WHERE role = 'headquarters_admin' ORDER BY user_id LIMIT 1"
            ).fetchone()
        if row is None:
            raise RuntimeError("No Headquarters administrator is configured.")
        return int(row["user_id"])

    def create_sector_account(self, sector_number: str, username: str | None = None) -> dict[str, object]:
        normalized = sector_number.strip().upper()
        if not normalized or len(normalized) > 32 or not all(ch.isalnum() or ch in "-_" for ch in normalized):
            raise ValueError("Sector number must contain only letters, numbers, hyphens, or underscores.")
        supplied_username = username.strip().casefold() if isinstance(username, str) and username.strip() else ""
        username = supplied_username or f"SECTOR-{normalized}"
        if len(username) > 254 or (supplied_username and "@" not in username):
            raise ValueError("Sector account login must be the registered email address.")
        temporary_password = secrets.token_urlsafe(15)
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT user_id, username FROM users WHERE role = 'sector' AND is_active = 1 AND sector_number = ?",
                (normalized,),
            ).fetchone()
            if existing:
                return {
                    "user_id": int(existing["user_id"]), "username": str(existing["username"]),
                    "sector_number": normalized, "created": False, "temporary_password": None,
                }
            cursor = connection.execute(
                "INSERT INTO users (username, password_hash, role, sector_number, must_change_password) "
                "VALUES (?, ?, 'sector', ?, 1)",
                (username, _hash_password(temporary_password), normalized),
            )
        return {
            "user_id": int(cursor.lastrowid), "username": username, "sector_number": normalized,
            "created": True, "temporary_password": temporary_password,
        }

    def decommission_sector_account(self, sector_number: str, reason: str) -> bool:
        """Disable and tombstone the shared account while preserving its login history."""
        normalized = sector_number.strip().upper()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT user_id, username FROM users WHERE role='sector' AND is_active=1 AND sector_number=?",
                (normalized,),
            ).fetchone()
            if row is None:
                return False
            user_id = int(row["user_id"])
            connection.execute(
                "INSERT INTO sector_account_history(user_id,username,sector_number,decommissioned_at,reason) VALUES(?,?,?,?,?)",
                (user_id, str(row["username"]), normalized, now, reason),
            )
            tombstone = f"decommissioned-{user_id}@invalid.invalid"
            connection.execute(
                "UPDATE users SET username=?, password_hash=?, sector_number=NULL, is_active=0, deactivated_at=?, must_change_password=1 WHERE user_id=?",
                (tombstone, _hash_password(secrets.token_urlsafe(32)), now, user_id),
            )
        self.invalidate_user_sessions(user_id)
        return True

    def delete_sector_account(self, sector_number: str) -> bool:
        normalized = sector_number.strip().upper()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT user_id FROM users WHERE role = 'sector' AND is_active = 1 AND sector_number = ?",
                (normalized,),
            ).fetchone()
            if row is None:
                return False
            user_id = int(row["user_id"])
            connection.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        self.invalidate_user_sessions(user_id)
        return True

    def reset_sector_password(self, sector_number: str) -> dict[str, object]:
        """Rotate a shared sector credential and force a change at next sign-in."""
        normalized = sector_number.strip().upper()
        temporary_password = secrets.token_urlsafe(18)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT user_id, username FROM users WHERE role = 'sector' AND is_active = 1 AND sector_number = ?",
                (normalized,),
            ).fetchone()
            if row is None:
                raise ValueError("No shared account exists for this sector.")
            connection.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 1 WHERE user_id = ?",
                (_hash_password(temporary_password), int(row["user_id"])),
            )
        return {
            "user_id": int(row["user_id"]), "username": str(row["username"]),
            "sector_number": normalized, "created": True,
            "temporary_password": temporary_password,
        }

    def invalidate_user_sessions(self, user_id: int) -> None:
        """Immediately invalidate all in-memory sessions for a shared account."""
        now = time.time()
        with self._lock:
            keys = [key for key, (uid, _name, _expiry) in self._sessions.items() if uid == user_id]
            for key in keys:
                self._close_session(key, now)
                self._sessions.pop(key, None)

    def change_password(self, user_id: int, current_password: str, new_password: str) -> bool:
        if not is_valid_new_password(new_password):
            raise ValueError("New password must be 8 to 12 characters and include an uppercase letter, a lowercase letter, a number, and a symbol.")
        with self._connection() as connection:
            row = connection.execute("SELECT password_hash FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if row is None or not _verify_password(current_password, str(row["password_hash"])):
                return False
            connection.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE user_id = ?",
                (_hash_password(new_password), user_id),
            )
        return True
    @staticmethod
    def _session_key(token: str) -> str | None:
        try:
            token_bytes = token.encode("ascii")
        except UnicodeEncodeError:
            return None
        return hashlib.sha256(token_bytes).hexdigest()

    def create_session(self, user_id: int, username: str, previous_token: str | None = None) -> str:
        now = time.time()
        with self._lock:
            if previous_token:
                previous_key = self._session_key(previous_token)
                if previous_key:
                    self._close_session(previous_key, now)
                    self._sessions.pop(previous_key, None)
            expired = [key for key, (_, _, expires_at) in self._sessions.items() if expires_at <= now]
            for key in expired:
                self._close_session(key, now)
                del self._sessions[key]
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
            self._sessions[token_hash] = (user_id, username, now + SESSION_TTL_SECONDS)
            self._session_history[token_hash] = {
                "session_id": secrets.token_hex(12),
                "user_id": user_id,
                "username": username,
                "logged_in_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "logged_out_at": None,
                "duration_seconds": None,
            }
            self._persist_session_history()
        return token

    def get_session(self, token: str | None) -> tuple[int, str] | None:
        if not token:
            return None
        token_hash = self._session_key(token)
        if token_hash is None:
            return None
        now = time.time()
        with self._lock:
            session = self._sessions.get(token_hash)
            if session is None:
                return None
            user_id, username, expires_at = session
            if expires_at <= now:
                self._close_session(token_hash, now)
                del self._sessions[token_hash]
                return None
            return user_id, username

    def invalidate_session(self, token: str | None) -> None:
        if not token:
            return
        token_hash = self._session_key(token)
        if token_hash is None:
            return
        with self._lock:
            self._close_session(token_hash, time.time())
            self._sessions.pop(token_hash, None)

    def _close_session(self, token_hash: str, ended_at: float) -> None:
        record = self._session_history.get(token_hash)
        if record is None or record["logged_out_at"] is not None:
            return
        started_epoch = datetime.strptime(
            str(record["logged_in_at"]), "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc).timestamp()
        record["logged_out_at"] = datetime.fromtimestamp(
            ended_at, timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        record["duration_seconds"] = max(0, int(ended_at - started_epoch))
        self._persist_session_history()
        self._session_history.pop(token_hash, None)

    def _persist_session_history(self) -> None:
        path = self.database_path.with_name("session_history.json")
        entries = []
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(loaded, list):
                    raise ValueError("Session history must be a JSON list")
                entries = loaded
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError("Could not read persisted session history") from error
        for token_hash, session in self._session_history.items():
            entry = dict(session)
            entry["active"] = session["logged_out_at"] is None
            entries = [item for item in entries if item.get("session_id") != entry["session_id"]]
            entries.append(entry)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(entries, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def _close_stale_sessions(self) -> None:
        path = self.database_path.with_name("session_history.json")
        if not path.is_file():
            return
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("Could not read persisted session history") from error
        if not isinstance(entries, list):
            raise RuntimeError("Persisted session history is malformed")
        now = time.time()
        updated = False
        for entry in entries:
            if not isinstance(entry, dict):
                raise RuntimeError("Persisted session history contains a malformed entry")
            if entry.get("logged_out_at") is not None:
                continue
            try:
                started = datetime.strptime(
                    str(entry["logged_in_at"]), "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=timezone.utc)
            except (KeyError, ValueError, TypeError) as error:
                raise RuntimeError("Persisted session history contains an invalid login time") from error
            entry["logged_out_at"] = datetime.fromtimestamp(
                now, timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            entry["duration_seconds"] = max(0, int(now - started.timestamp()))
            entry["active"] = False
            updated = True
        if updated:
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(entries, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)

    def session_history(self, user_id: int) -> list[dict[str, object]]:
        path = self.database_path.with_name("session_history.json")
        if not path.is_file():
            return []
        with self._lock:
            try:
                entries = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError("Could not read persisted session history") from error
        if not isinstance(entries, list):
            raise RuntimeError("Persisted session history is malformed")
        now = time.time()
        result = []
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("user_id") != user_id:
                continue
            item = dict(entry)
            if item.get("logged_out_at") is None:
                try:
                    started = datetime.strptime(
                        str(item["logged_in_at"]), "%Y-%m-%dT%H:%M:%SZ"
                    ).replace(tzinfo=timezone.utc)
                    item["duration_seconds"] = max(0, int(now - started.timestamp()))
                except (KeyError, ValueError, TypeError):
                    item["duration_seconds"] = None
            result.append(item)
        return sorted(result, key=lambda item: str(item.get("logged_in_at", "")), reverse=True)


def cookie_header(token: str, secure: bool) -> str:
    cookie = (
        f"{SESSION_COOKIE_NAME}={token}; Path=/; Max-Age={SESSION_TTL_SECONDS}; "
        "HttpOnly; SameSite=Strict"
    )
    return f"{cookie}; Secure" if secure else cookie


def expired_cookie_header(secure: bool) -> str:
    cookie = f"{SESSION_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
    return f"{cookie}; Secure" if secure else cookie


def secure_cookies_enabled() -> bool:
    return os.environ.get("BTF_COOKIE_SECURE", "").strip().casefold() in {"1", "true", "yes"}
