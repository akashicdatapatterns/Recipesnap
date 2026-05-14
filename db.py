import hashlib
import os
import re
import secrets
from base64 import b64decode, b64encode
from contextlib import contextmanager
from typing import Any, Optional

import sqlite3

try:
    import psycopg2
    import psycopg2.errors
    import psycopg2.extras
except ImportError:
    psycopg2 = None
    psycopg2_errors = None
    psycopg2_extras = None


DB_PATH = os.getenv("SQLITE_DB_PATH", os.path.join(os.path.dirname(__file__), "culinaryvault.db"))
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _get_db_path() -> str:
    """Return the SQLite database path used for local development."""
    return DB_PATH


def _using_postgres() -> bool:
    return bool(DATABASE_URL)


def _row_to_dict(row) -> Optional[dict]:
    """Convert a sqlite row to a plain dict."""
    if row is None:
        return None
    result: dict = {}
    for k, v in dict(row).items():
        if isinstance(v, memoryview):
            result[k] = bytes(v)
        elif hasattr(v, "isoformat"):
            result[k] = v.isoformat()
        else:
            result[k] = v
    return result


def _binary(data) -> Optional[Any]:
    """Wrap bytes for BYTEA insertion; pass through None."""
    if data is None:
        return None
    if isinstance(data, (bytes, bytearray, memoryview)):
        if _using_postgres():
            return psycopg2.Binary(bytes(data)) if psycopg2 is not None else bytes(data)
        return sqlite3.Binary(bytes(data))
    return data


def _integrity_errors() -> tuple[type[Exception], ...]:
    errors: list[type[Exception]] = [sqlite3.IntegrityError]
    if psycopg2 is not None:
        errors.append(psycopg2.IntegrityError)
        if hasattr(psycopg2, "errors"):
            errors.append(psycopg2.errors.UniqueViolation)
    return tuple(errors)


def _translate_sql(sql: str) -> str:
    translated = sql.replace("NOW()", "CURRENT_TIMESTAMP")
    translated = translated.replace("%s", "?")
    return translated


class _Conn:
    """Thin wrapper around a database cursor.

    Exposes the same conn.execute(sql, params).fetchall() API used
    throughout this module so query functions need minimal changes.
    """

    def __init__(self, connection: Any, cursor: Any, backend: str) -> None:
        self._connection = connection
        self._cur = cursor
        self._backend = backend

    def execute(self, sql: str, params=None) -> "_Conn":
        if self._backend == "sqlite":
            self._cur.execute(_translate_sql(sql), params or ())
        else:
            self._cur.execute(sql, params or ())
        return self

    def fetchone(self) -> Optional[dict]:
        return _row_to_dict(self._cur.fetchone())

    def fetchall(self) -> list:
        rows = self._cur.fetchall() or []
        return [_row_to_dict(r) for r in rows]

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount or 0

    @property
    def lastrowid(self) -> int:
        return int(self._cur.lastrowid or 0)


@contextmanager
def get_connection():
    """Yield a _Conn wrapping a database transaction.
    Commits on clean exit; rolls back + re-raises on any exception.
    Always closes the underlying connection.
    """
    if _using_postgres():
        if psycopg2 is None:
            raise RuntimeError("psycopg2 is required when DATABASE_URL is set.")
        try:
            raw = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        except psycopg2.OperationalError as exc:
            raise RuntimeError(f"PostgreSQL database unavailable: {exc}") from exc
        wrapped = _Conn(raw, raw.cursor(), backend="postgres")
        try:
            yield wrapped
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()
    else:
        raw = sqlite3.connect(_get_db_path())
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys = ON")
        wrapped = _Conn(raw, raw.cursor(), backend="sqlite")
        try:
            yield wrapped
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()


def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}{password}".encode("utf-8")).hexdigest()


def _recipe_scope_clause(user_id: Optional[int], is_admin: bool, table_alias: str = "recipes") -> tuple:
    if is_admin:
        return "", []
    safe_user_id = int(user_id or -1)
    return f" AND {table_alias}.user_id = %s", [safe_user_id]


def init_db() -> None:
    with get_connection() as conn:
        if _using_postgres():
            def _has_column(table: str, column: str) -> bool:
                row = conn.execute(
                    "SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
                    (table, column),
                ).fetchone()
                return row is not None

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id                SERIAL PRIMARY KEY,
                    username          VARCHAR(150) NOT NULL UNIQUE,
                    email             VARCHAR(255) NOT NULL UNIQUE,
                    full_name         TEXT,
                    phone             VARCHAR(30),
                    city              TEXT,
                    country           TEXT,
                    cooking_preference TEXT,
                    password_salt     VARCHAR(64) NOT NULL,
                    password_hash     VARCHAR(128) NOT NULL,
                    is_admin          SMALLINT NOT NULL DEFAULT 0 CHECK (is_admin IN (0,1)),
                    is_blocked        SMALLINT NOT NULL DEFAULT 0 CHECK (is_blocked IN (0,1)),
                    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS recipes (
                    id                   SERIAL PRIMARY KEY,
                    user_id              INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                    title                TEXT NOT NULL,
                    description          TEXT,
                    ingredients          TEXT NOT NULL,
                    instructions         TEXT NOT NULL,
                    tips_for_best_result TEXT,
                    servings             INTEGER NOT NULL DEFAULT 1 CHECK (servings >= 1),
                    prep_time            INTEGER NOT NULL DEFAULT 0 CHECK (prep_time >= 0),
                    cook_time            INTEGER NOT NULL DEFAULT 0 CHECK (cook_time >= 0),
                    difficulty           VARCHAR(20) NOT NULL DEFAULT 'Easy' CHECK (difficulty IN ('Easy','Medium','Hard')),
                    category             TEXT,
                    reference_url        TEXT,
                    tags                 TEXT,
                    image                BYTEA,
                    ingredient_format    VARCHAR(50) NOT NULL DEFAULT 'quantity_item',
                    is_favorite          SMALLINT NOT NULL DEFAULT 0 CHECK (is_favorite IN (0,1)),
                    rating               DOUBLE PRECISION CHECK (rating IS NULL OR (rating >= 0 AND rating <= 5)),
                    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meal_plan_entries (
                    id          SERIAL PRIMARY KEY,
                    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    recipe_id   INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
                    meal_date   TEXT NOT NULL,
                    meal_type   TEXT NOT NULL,
                    notes       TEXT,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_meal_plan_date   ON meal_plan_entries(meal_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_meal_plan_recipe ON meal_plan_entries(recipe_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_recipes_user     ON recipes(user_id)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_sessions (
                    id         SERIAL PRIMARY KEY,
                    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token      VARCHAR(128) NOT NULL UNIQUE,
                    expires_at TIMESTAMPTZ NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_user_sessions_token ON user_sessions(token)")

            if not _has_column("recipes", "is_favorite"):
                conn.execute("ALTER TABLE recipes ADD COLUMN is_favorite SMALLINT NOT NULL DEFAULT 0")
            if not _has_column("recipes", "rating"):
                conn.execute("ALTER TABLE recipes ADD COLUMN rating DOUBLE PRECISION")
            if not _has_column("recipes", "servings"):
                conn.execute("ALTER TABLE recipes ADD COLUMN servings INTEGER NOT NULL DEFAULT 1")
            if not _has_column("recipes", "tips_for_best_result"):
                conn.execute("ALTER TABLE recipes ADD COLUMN tips_for_best_result TEXT")
            if not _has_column("recipes", "reference_url"):
                conn.execute("ALTER TABLE recipes ADD COLUMN reference_url TEXT")
            if not _has_column("recipes", "ingredient_format"):
                conn.execute("ALTER TABLE recipes ADD COLUMN ingredient_format VARCHAR(50) NOT NULL DEFAULT 'quantity_item'")
            if not _has_column("recipes", "user_id"):
                conn.execute("ALTER TABLE recipes ADD COLUMN user_id INTEGER")
            if not _has_column("meal_plan_entries", "user_id"):
                conn.execute("ALTER TABLE meal_plan_entries ADD COLUMN user_id INTEGER")
            if not _has_column("users", "full_name"):
                conn.execute("ALTER TABLE users ADD COLUMN full_name TEXT")
            if not _has_column("users", "phone"):
                conn.execute("ALTER TABLE users ADD COLUMN phone VARCHAR(30)")
            if not _has_column("users", "city"):
                conn.execute("ALTER TABLE users ADD COLUMN city TEXT")
            if not _has_column("users", "country"):
                conn.execute("ALTER TABLE users ADD COLUMN country TEXT")
            if not _has_column("users", "cooking_preference"):
                conn.execute("ALTER TABLE users ADD COLUMN cooking_preference TEXT")
            if not _has_column("users", "is_blocked"):
                conn.execute("ALTER TABLE users ADD COLUMN is_blocked SMALLINT NOT NULL DEFAULT 0")
            if not _has_column("users", "openai_api_key"):
                conn.execute("ALTER TABLE users ADD COLUMN openai_api_key TEXT")

            admin_user = conn.execute("SELECT id FROM users WHERE is_admin = 1 ORDER BY id ASC LIMIT 1").fetchone()
            if not admin_user:
                admin_salt = secrets.token_hex(16)
                admin_hash = _hash_password("admin123", admin_salt)
                row = conn.execute(
                    """
                    INSERT INTO users (username, email, password_salt, password_hash, is_admin)
                    VALUES (%s, %s, %s, %s, 1)
                    RETURNING id
                    """,
                    ("admin", "admin@recipesnap.local", admin_salt, admin_hash),
                ).fetchone()
                admin_id = int(row["id"])
            else:
                admin_id = int(admin_user["id"])

            conn.execute("UPDATE recipes SET user_id = %s WHERE user_id IS NULL", (admin_id,))
            conn.execute(
                """
                UPDATE meal_plan_entries
                SET user_id = COALESCE(
                    user_id,
                    (SELECT r.user_id FROM recipes r WHERE r.id = meal_plan_entries.recipe_id),
                    %s
                )
                WHERE user_id IS NULL
                """,
                (admin_id,),
            )
        else:
            def _has_column(table: str, column: str) -> bool:
                rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
                return any(str(row["name"]) == column for row in rows)

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    username           TEXT NOT NULL UNIQUE,
                    email              TEXT NOT NULL UNIQUE,
                    full_name          TEXT,
                    phone              TEXT,
                    city               TEXT,
                    country            TEXT,
                    cooking_preference TEXT,
                    password_salt      TEXT NOT NULL,
                    password_hash      TEXT NOT NULL,
                    is_admin           INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0,1)),
                    is_blocked         INTEGER NOT NULL DEFAULT 0 CHECK (is_blocked IN (0,1)),
                    created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS recipes (
                    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id              INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                    title                TEXT NOT NULL,
                    description          TEXT,
                    ingredients          TEXT NOT NULL,
                    instructions         TEXT NOT NULL,
                    tips_for_best_result TEXT,
                    servings             INTEGER NOT NULL DEFAULT 1 CHECK (servings >= 1),
                    prep_time            INTEGER NOT NULL DEFAULT 0 CHECK (prep_time >= 0),
                    cook_time            INTEGER NOT NULL DEFAULT 0 CHECK (cook_time >= 0),
                    difficulty           TEXT NOT NULL DEFAULT 'Easy' CHECK (difficulty IN ('Easy','Medium','Hard')),
                    category             TEXT,
                    reference_url        TEXT,
                    tags                 TEXT,
                    image                BLOB,
                    ingredient_format    TEXT NOT NULL DEFAULT 'quantity_item',
                    is_favorite          INTEGER NOT NULL DEFAULT 0 CHECK (is_favorite IN (0,1)),
                    rating               REAL CHECK (rating IS NULL OR (rating >= 0 AND rating <= 5)),
                    created_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meal_plan_entries (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    recipe_id   INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
                    meal_date   TEXT NOT NULL,
                    meal_type   TEXT NOT NULL,
                    notes       TEXT,
                    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_sessions (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token      TEXT NOT NULL UNIQUE,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_meal_plan_date   ON meal_plan_entries(meal_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_meal_plan_recipe ON meal_plan_entries(recipe_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_recipes_user     ON recipes(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_user_sessions_token ON user_sessions(token)")

            if not _has_column("recipes", "is_favorite"):
                conn.execute("ALTER TABLE recipes ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0")
            if not _has_column("recipes", "rating"):
                conn.execute("ALTER TABLE recipes ADD COLUMN rating REAL")
            if not _has_column("recipes", "servings"):
                conn.execute("ALTER TABLE recipes ADD COLUMN servings INTEGER NOT NULL DEFAULT 1")
            if not _has_column("recipes", "tips_for_best_result"):
                conn.execute("ALTER TABLE recipes ADD COLUMN tips_for_best_result TEXT")
            if not _has_column("recipes", "reference_url"):
                conn.execute("ALTER TABLE recipes ADD COLUMN reference_url TEXT")
            if not _has_column("recipes", "ingredient_format"):
                conn.execute("ALTER TABLE recipes ADD COLUMN ingredient_format TEXT NOT NULL DEFAULT 'quantity_item'")
            if not _has_column("recipes", "user_id"):
                conn.execute("ALTER TABLE recipes ADD COLUMN user_id INTEGER")
            if not _has_column("meal_plan_entries", "user_id"):
                conn.execute("ALTER TABLE meal_plan_entries ADD COLUMN user_id INTEGER")
            if not _has_column("users", "full_name"):
                conn.execute("ALTER TABLE users ADD COLUMN full_name TEXT")
            if not _has_column("users", "phone"):
                conn.execute("ALTER TABLE users ADD COLUMN phone TEXT")
            if not _has_column("users", "city"):
                conn.execute("ALTER TABLE users ADD COLUMN city TEXT")
            if not _has_column("users", "country"):
                conn.execute("ALTER TABLE users ADD COLUMN country TEXT")
            if not _has_column("users", "cooking_preference"):
                conn.execute("ALTER TABLE users ADD COLUMN cooking_preference TEXT")
            if not _has_column("users", "is_blocked"):
                conn.execute("ALTER TABLE users ADD COLUMN is_blocked INTEGER NOT NULL DEFAULT 0")
            if not _has_column("users", "openai_api_key"):
                conn.execute("ALTER TABLE users ADD COLUMN openai_api_key TEXT")

            admin_user = conn.execute("SELECT id FROM users WHERE is_admin = 1 ORDER BY id ASC LIMIT 1").fetchone()
            if not admin_user:
                admin_salt = secrets.token_hex(16)
                admin_hash = _hash_password("admin123", admin_salt)
                conn.execute(
                    """
                    INSERT INTO users (username, email, password_salt, password_hash, is_admin)
                    VALUES (?, ?, ?, ?, 1)
                    """,
                    ("admin", "admin@recipesnap.local", admin_salt, admin_hash),
                )
                admin_id = conn.lastrowid
            else:
                admin_id = int(admin_user["id"])

            conn.execute("UPDATE recipes SET user_id = ? WHERE user_id IS NULL", (admin_id,))
            conn.execute(
                """
                UPDATE meal_plan_entries
                SET user_id = COALESCE(
                    user_id,
                    (SELECT r.user_id FROM recipes r WHERE r.id = meal_plan_entries.recipe_id),
                    ?
                )
                WHERE user_id IS NULL
                """,
                (admin_id,),
            )


def create_user(
    username: str,
    email: str,
    password: str,
    full_name: Optional[str] = None,
    phone: Optional[str] = None,
    city: Optional[str] = None,
    country: Optional[str] = None,
    cooking_preference: Optional[str] = None,
    is_admin: bool = False,
) -> tuple[bool, str]:
    clean_username = (username or "").strip()
    clean_email = (email or "").strip().lower()
    clean_full_name = (full_name or "").strip()
    clean_phone = (phone or "").strip()
    clean_city = (city or "").strip()
    clean_country = (country or "").strip()
    clean_cooking_preference = (cooking_preference or "").strip()
    if len(clean_username) < 3:
        return False, "Username must be at least 3 characters."
    if "@" not in clean_email or "." not in clean_email:
        return False, "Please provide a valid email address."
    if len(clean_full_name) < 2:
        return False, "Full name must be at least 2 characters."
    if clean_phone and not re.fullmatch(r"[0-9+\-()\s]{7,20}", clean_phone):
        return False, "Phone number format is invalid."
    if len(password or "") < 6:
        return False, "Password must be at least 6 characters."

    salt = secrets.token_hex(16)
    password_hash = _hash_password(password, salt)

    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    username, email, full_name, phone, city, country, cooking_preference,
                    password_salt, password_hash, is_admin, is_blocked
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0)
                """,
                (
                    clean_username,
                    clean_email,
                    clean_full_name,
                    clean_phone,
                    clean_city,
                    clean_country,
                    clean_cooking_preference,
                    salt,
                    password_hash,
                    1 if is_admin else 0,
                ),
            )
        return True, "Registration successful. You can log in now."
    except _integrity_errors():
        return False, "Username or email already exists."


def authenticate_user(identifier: str, password: str) -> Optional[dict]:
    login = (identifier or "").strip()
    if not login or not password:
        return None

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, username, email, full_name, phone, city, country, cooking_preference,
                   password_salt, password_hash, is_admin, is_blocked, created_at
            FROM users
            WHERE lower(username) = lower(%s) OR lower(email) = lower(%s)
            LIMIT 1
            """,
            (login, login),
        ).fetchone()

    if not row:
        return None

    expected = _hash_password(password, str(row["password_salt"]))
    if expected != str(row["password_hash"]):
        return None
    if bool(int(row["is_blocked"] or 0)):
        return None

    return {
        "id": int(row["id"]),
        "username": str(row["username"]),
        "email": str(row["email"]),
        "full_name": str(row["full_name"] or ""),
        "phone": str(row["phone"] or ""),
        "city": str(row["city"] or ""),
        "country": str(row["country"] or ""),
        "cooking_preference": str(row["cooking_preference"] or ""),
        "is_admin": bool(int(row["is_admin"] or 0)),
        "is_blocked": bool(int(row["is_blocked"] or 0)),
        "created_at": str(row["created_at"] or ""),
    }


def save_openai_api_key(user_id: int, api_key: str) -> bool:
    """Save the OpenAI API key for a user."""
    try:
        with get_connection() as conn:
            conn.execute(
                "UPDATE users SET openai_api_key = %s WHERE id = %s",
                (api_key if api_key else None, user_id),
            )
        return True
    except Exception:
        return False


def get_openai_api_key(user_id: int) -> Optional[str]:
    """Retrieve the OpenAI API key for a user."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT openai_api_key FROM users WHERE id = %s",
                (user_id,),
            ).fetchone()
        return str(row["openai_api_key"]) if row and row["openai_api_key"] else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Persistent session helpers (remember-me / auto-login via cookie)
# ---------------------------------------------------------------------------

SESSION_DURATION_DAYS = 30


def create_user_session(user_id: int) -> str:
    """Create a new persistent session token for *user_id* and return it.
    Tokens are valid for SESSION_DURATION_DAYS days.
    Old expired sessions for this user are pruned at the same time.
    """
    import datetime as _dt

    token = secrets.token_hex(48)  # 96-char hex string
    now = _dt.datetime.utcnow()
    expires = now + _dt.timedelta(days=SESSION_DURATION_DAYS)
    expires_str = expires.isoformat()

    with get_connection() as conn:
        # Prune expired sessions for this user
        conn.execute(
            "DELETE FROM user_sessions WHERE user_id = %s AND expires_at < %s",
            (user_id, now.isoformat()),
        )
        conn.execute(
            "INSERT INTO user_sessions (user_id, token, expires_at) VALUES (%s, %s, %s)",
            (user_id, token, expires_str),
        )
    return token


def get_session_user(token: str) -> Optional[dict]:
    """Validate *token* and return the user dict if the session is still valid."""
    import datetime as _dt

    if not token:
        return None
    now = _dt.datetime.utcnow().isoformat()
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT u.id, u.username, u.email, u.full_name, u.phone, u.city, u.country,
                       u.cooking_preference, u.is_admin, u.is_blocked, u.created_at
                FROM user_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token = %s AND s.expires_at > %s
                LIMIT 1
                """,
                (token, now),
            ).fetchone()
    except Exception:
        return None

    if not row:
        return None
    if bool(int(row.get("is_blocked") or 0)):
        return None
    return {
        "id": int(row["id"]),
        "username": str(row["username"]),
        "email": str(row["email"]),
        "full_name": str(row["full_name"] or ""),
        "phone": str(row["phone"] or ""),
        "city": str(row["city"] or ""),
        "country": str(row["country"] or ""),
        "cooking_preference": str(row["cooking_preference"] or ""),
        "is_admin": bool(int(row["is_admin"] or 0)),
        "is_blocked": False,
        "created_at": str(row["created_at"] or ""),
    }


def delete_user_session(token: str) -> None:
    """Delete a single session token (logout)."""
    if not token:
        return
    try:
        with get_connection() as conn:
            conn.execute("DELETE FROM user_sessions WHERE token = %s", (token,))
    except Exception:
        pass


def reset_user_password(username: str, email: str, new_password: str) -> tuple[bool, str]:
    clean_username = (username or "").strip()
    clean_email = (email or "").strip().lower()
    if len(new_password or "") < 6:
        return False, "Password must be at least 6 characters."

    salt = secrets.token_hex(16)
    password_hash = _hash_password(new_password, salt)

    with get_connection() as conn:
        cur = conn.execute(
            """
            UPDATE users
            SET password_salt = %s,
                password_hash = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE lower(username) = lower(%s) AND lower(email) = lower(%s)
            """,
            (salt, password_hash, clean_username, clean_email),
        )
    if int(cur.rowcount or 0) == 0:
        return False, "No user found with the provided username and email."
    return True, "Password reset successful. Please log in."


def get_user_by_id(user_id: int) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, username, email, full_name, phone, city, country, cooking_preference,
                   is_admin, is_blocked, created_at, updated_at
            FROM users
            WHERE id = %s
            """,
            (int(user_id),),
        ).fetchone()
    return dict(row) if row else None


def update_user_profile(
    user_id: int,
    username: str,
    email: str,
    full_name: str,
    phone: Optional[str] = None,
    city: Optional[str] = None,
    country: Optional[str] = None,
    cooking_preference: Optional[str] = None,
    new_password: Optional[str] = None,
) -> tuple[bool, str]:
    clean_username = (username or "").strip()
    clean_email = (email or "").strip().lower()
    clean_full_name = (full_name or "").strip()
    clean_phone = (phone or "").strip()
    clean_city = (city or "").strip()
    clean_country = (country or "").strip()
    clean_cooking_preference = (cooking_preference or "").strip()

    if len(clean_username) < 3:
        return False, "Username must be at least 3 characters."
    if "@" not in clean_email or "." not in clean_email:
        return False, "Please provide a valid email address."
    if len(clean_full_name) < 2:
        return False, "Full name must be at least 2 characters."
    if clean_phone and not re.fullmatch(r"[0-9+\-()\s]{7,20}", clean_phone):
        return False, "Phone number format is invalid."
    if new_password and len(new_password) < 6:
        return False, "New password must be at least 6 characters."

    password_salt = None
    password_hash = None
    if new_password:
        password_salt = secrets.token_hex(16)
        password_hash = _hash_password(new_password, password_salt)

    try:
        with get_connection() as conn:
            if new_password:
                conn.execute(
                    """
                    UPDATE users
                    SET username = %s,
                        email = %s,
                        full_name = %s,
                        phone = %s,
                        city = %s,
                        country = %s,
                        cooking_preference = %s,
                        password_salt = %s,
                        password_hash = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (
                        clean_username,
                        clean_email,
                        clean_full_name,
                        clean_phone,
                        clean_city,
                        clean_country,
                        clean_cooking_preference,
                        password_salt,
                        password_hash,
                        int(user_id),
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE users
                    SET username = %s,
                        email = %s,
                        full_name = %s,
                        phone = %s,
                        city = %s,
                        country = %s,
                        cooking_preference = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (
                        clean_username,
                        clean_email,
                        clean_full_name,
                        clean_phone,
                        clean_city,
                        clean_country,
                        clean_cooking_preference,
                        int(user_id),
                    ),
                )
        return True, "Profile updated successfully."
    except _integrity_errors():
        return False, "Username or email is already in use by another account."


def list_users_with_stats() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                u.id,
                u.username,
                u.email,
                u.full_name,
                u.phone,
                u.city,
                u.country,
                u.cooking_preference,
                u.is_admin,
                u.is_blocked,
                u.created_at,
                u.updated_at,
                COALESCE(COUNT(r.id), 0) AS recipe_count
            FROM users u
            LEFT JOIN recipes r ON r.user_id = u.id
            GROUP BY u.id, u.username, u.email, u.full_name, u.phone, u.city, u.country, u.cooking_preference,
                     u.is_admin, u.is_blocked, u.created_at, u.updated_at
            ORDER BY u.created_at DESC, u.username ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def set_user_blocked(target_user_id: int, blocked: bool) -> bool:
    with get_connection() as conn:
        cur = conn.execute(
            """
            UPDATE users
            SET is_blocked = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (1 if blocked else 0, int(target_user_id)),
        )
    return int(cur.rowcount or 0) > 0


def create_recipe(payload: dict, user_id: int) -> int:
    with get_connection() as conn:
        params = (
            int(user_id),
            payload["title"],
            payload.get("description"),
            payload["ingredients"],
            payload["instructions"],
            payload.get("tips_for_best_result"),
            max(1, int(payload.get("servings", 1) or 1)),
            payload.get("prep_time", 0),
            payload.get("cook_time", 0),
            payload.get("difficulty", "Easy"),
            payload.get("category"),
            payload.get("reference_url"),
            payload.get("tags"),
            payload.get("image"),
            int(payload.get("is_favorite", 0) or 0),
            payload.get("rating"),
            payload.get("ingredient_format", "quantity_item"),
        )
        if _using_postgres():
            row = conn.execute(
                """
                INSERT INTO recipes
                    (user_id, title, description, ingredients, instructions, tips_for_best_result,
                     servings, prep_time, cook_time, difficulty, category, reference_url, tags,
                     image, is_favorite, rating, ingredient_format)
                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                params,
            ).fetchone()
            return int(row["id"])

        cur = conn.execute(
            """
            INSERT INTO recipes
                (user_id, title, description, ingredients, instructions, tips_for_best_result,
                 servings, prep_time, cook_time, difficulty, category, reference_url, tags,
                 image, is_favorite, rating, ingredient_format)
              VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            params,
        )
        return int(cur.lastrowid)


def list_recipes(
    search: Optional[str] = None,
    category: Optional[str] = None,
    difficulty: Optional[str] = None,
    favorites_only: bool = False,
    min_rating: Optional[float] = None,
    user_id: Optional[int] = None,
    is_admin: bool = False,
) -> list[dict]:
    query = "SELECT * FROM recipes WHERE 1=1"
    params: list[object] = []

    scope_clause, scope_params = _recipe_scope_clause(user_id=user_id, is_admin=is_admin)
    query += scope_clause
    params.extend(scope_params)

    if search:
        query += """
            AND (
                lower(title) LIKE lower(%s) OR
                lower(description) LIKE lower(%s) OR
                lower(ingredients) LIKE lower(%s) OR
                lower(instructions) LIKE lower(%s) OR
                lower(reference_url) LIKE lower(%s) OR
                lower(tags) LIKE lower(%s)
            )
        """
        needle = f"%{search}%"
        params.extend([needle, needle, needle, needle, needle, needle])

    if category:
        query += " AND category = %s"
        params.append(category)

    if difficulty:
        query += " AND difficulty = %s"
        params.append(difficulty)

    if favorites_only:
        query += " AND is_favorite = 1"

    if min_rating is not None:
        query += " AND COALESCE(rating, 0) >= %s"
        params.append(float(min_rating))

    query += " ORDER BY updated_at DESC, title ASC"

    with get_connection() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()

    return [dict(row) for row in rows]


def get_recipe(recipe_id: int, user_id: Optional[int] = None, is_admin: bool = False) -> Optional[dict]:
    query = "SELECT * FROM recipes WHERE id = %s"
    params: list[object] = [int(recipe_id)]
    scope_clause, scope_params = _recipe_scope_clause(user_id=user_id, is_admin=is_admin)
    query += scope_clause
    params.extend(scope_params)
    with get_connection() as conn:
        row = conn.execute(query, tuple(params)).fetchone()
    return row


def update_recipe(recipe_id: int, payload: dict, user_id: Optional[int] = None, is_admin: bool = False) -> bool:
    query = """
        UPDATE recipes
        SET
            title = %s,
            description = %s,
            ingredients = %s,
            instructions = %s,
            tips_for_best_result = %s,
            servings = %s,
            prep_time = %s,
            cook_time = %s,
            difficulty = %s,
            category = %s,
            reference_url = %s,
            tags = %s,
            image = %s,
            is_favorite = %s,
            rating = %s,
            ingredient_format = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """
    params: list[object] = [
        payload["title"],
        payload.get("description"),
        payload["ingredients"],
        payload["instructions"],
        payload.get("tips_for_best_result"),
        max(1, int(payload.get("servings", 1) or 1)),
        payload.get("prep_time", 0),
        payload.get("cook_time", 0),
        payload.get("difficulty", "Easy"),
        payload.get("category"),
        payload.get("reference_url"),
        payload.get("tags"),
        payload.get("image"),
        int(payload.get("is_favorite", 0) or 0),
        payload.get("rating"),
        payload.get("ingredient_format", "quantity_item"),
        int(recipe_id),
    ]

    scope_clause, scope_params = _recipe_scope_clause(user_id=user_id, is_admin=is_admin)
    query += scope_clause
    params.extend(scope_params)

    with get_connection() as conn:
        cur = conn.execute(query, tuple(params))
    return int(cur.rowcount or 0) > 0


def delete_recipe(recipe_id: int, user_id: Optional[int] = None, is_admin: bool = False) -> bool:
    query = "DELETE FROM recipes WHERE id = %s"
    params: list[object] = [int(recipe_id)]
    scope_clause, scope_params = _recipe_scope_clause(user_id=user_id, is_admin=is_admin)
    query += scope_clause
    params.extend(scope_params)

    with get_connection() as conn:
        cur = conn.execute(query, tuple(params))
    return int(cur.rowcount or 0) > 0


def set_favorite(recipe_id: int, is_favorite: bool, user_id: Optional[int] = None, is_admin: bool = False) -> bool:
    query = """
        UPDATE recipes
        SET is_favorite = %s, updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """
    params: list[object] = [1 if is_favorite else 0, int(recipe_id)]
    scope_clause, scope_params = _recipe_scope_clause(user_id=user_id, is_admin=is_admin)
    query += scope_clause
    params.extend(scope_params)

    with get_connection() as conn:
        cur = conn.execute(query, tuple(params))
    return int(cur.rowcount or 0) > 0


def set_rating(recipe_id: int, rating: Optional[float], user_id: Optional[int] = None, is_admin: bool = False) -> bool:
    sanitized = None if rating is None else max(0.0, min(5.0, float(rating)))
    query = """
        UPDATE recipes
        SET rating = %s, updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """
    params: list[object] = [sanitized, int(recipe_id)]
    scope_clause, scope_params = _recipe_scope_clause(user_id=user_id, is_admin=is_admin)
    query += scope_clause
    params.extend(scope_params)

    with get_connection() as conn:
        cur = conn.execute(query, tuple(params))
    return int(cur.rowcount or 0) > 0


def list_recipe_options(user_id: Optional[int] = None, is_admin: bool = False) -> list[tuple[int, str]]:
    query = "SELECT id, title FROM recipes WHERE 1=1"
    params: list[object] = []
    scope_clause, scope_params = _recipe_scope_clause(user_id=user_id, is_admin=is_admin)
    query += scope_clause
    params.extend(scope_params)
    query += " ORDER BY title ASC"

    with get_connection() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [(int(row["id"]), str(row["title"])) for row in rows]


def get_categories(user_id: Optional[int] = None, is_admin: bool = False) -> list[str]:
    query = """
        SELECT DISTINCT category
        FROM recipes
        WHERE category IS NOT NULL AND TRIM(category) <> ''
    """
    params: list[object] = []
    scope_clause, scope_params = _recipe_scope_clause(user_id=user_id, is_admin=is_admin)
    query += scope_clause
    params.extend(scope_params)
    query += " ORDER BY category ASC"

    with get_connection() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [str(row["category"]) for row in rows]


def get_difficulties(user_id: Optional[int] = None, is_admin: bool = False) -> list[str]:
    query = """
        SELECT DISTINCT difficulty
        FROM recipes
        WHERE difficulty IS NOT NULL AND TRIM(difficulty) <> ''
    """
    params: list[object] = []
    scope_clause, scope_params = _recipe_scope_clause(user_id=user_id, is_admin=is_admin)
    query += scope_clause
    params.extend(scope_params)
    query += """
        ORDER BY CASE difficulty
            WHEN 'Easy' THEN 1
            WHEN 'Medium' THEN 2
            WHEN 'Hard' THEN 3
            ELSE 99
        END
    """

    with get_connection() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [str(row["difficulty"]) for row in rows]


def export_recipes_records(user_id: Optional[int] = None, is_admin: bool = False) -> list[dict]:
    query = """
        SELECT
            title,
            description,
            ingredients,
            instructions,
            tips_for_best_result,
            servings,
            prep_time,
            cook_time,
            difficulty,
            category,
            reference_url,
            tags,
            is_favorite,
            rating,
            image
        FROM recipes
        WHERE 1=1
    """
    params: list[object] = []
    scope_clause, scope_params = _recipe_scope_clause(user_id=user_id, is_admin=is_admin)
    query += scope_clause
    params.extend(scope_params)
    query += " ORDER BY title ASC"

    with get_connection() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()

    exported: list[dict] = []
    for row in rows:
        item = dict(row)
        image_blob = item.get("image")
        item["image_base64"] = b64encode(image_blob).decode("ascii") if image_blob else None
        item.pop("image", None)
        exported.append(item)
    return exported


def import_recipes_records(recipes: list[dict], user_id: int) -> int:
    if not recipes:
        return 0

    created = 0
    with get_connection() as conn:
        for recipe in recipes:
            title = str(recipe.get("title", "")).strip()
            ingredients = str(recipe.get("ingredients", "")).strip()
            instructions = str(recipe.get("instructions", "")).strip()
            if not title or not ingredients or not instructions:
                continue

            image_base64 = recipe.get("image_base64")
            image_blob = b64decode(image_base64) if image_base64 else None

            conn.execute(
                """
                INSERT INTO recipes
                (title, description, ingredients, instructions, tips_for_best_result, servings, prep_time, cook_time, difficulty, category, reference_url, tags, image, is_favorite, rating, user_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    title,
                    recipe.get("description"),
                    ingredients,
                    instructions,
                    recipe.get("tips_for_best_result"),
                    max(1, int(recipe.get("servings", 1) or 1)),
                    int(recipe.get("prep_time", 0) or 0),
                    int(recipe.get("cook_time", 0) or 0),
                    recipe.get("difficulty", "Easy"),
                    recipe.get("category"),
                    recipe.get("reference_url"),
                    recipe.get("tags"),
                    image_blob,
                    int(recipe.get("is_favorite", 0) or 0),
                    recipe.get("rating"),
                    int(user_id),
                ),
            )
            created += 1
    return created


# Backward-compatible aliases.
def export_recipes_for_json(user_id: Optional[int] = None, is_admin: bool = False) -> list[dict]:
    return export_recipes_records(user_id=user_id, is_admin=is_admin)


def import_recipes_from_json(recipes: list[dict], user_id: int) -> int:
    return import_recipes_records(recipes, user_id=user_id)


def add_meal_plan_entry(
    meal_date: str,
    meal_type: str,
    recipe_id: int,
    notes: Optional[str] = None,
    user_id: Optional[int] = None,
    is_admin: bool = False,
) -> int:
    recipe = get_recipe(recipe_id=int(recipe_id), user_id=user_id, is_admin=is_admin)
    if not recipe:
        return 0

    with get_connection() as conn:
        params = (meal_date, meal_type, int(recipe_id), notes, int(user_id or recipe.get("user_id") or -1))
        if _using_postgres():
            row = conn.execute(
                """
                INSERT INTO meal_plan_entries (meal_date, meal_type, recipe_id, notes, user_id)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                params,
            ).fetchone()
            return int(row["id"])

        cur = conn.execute(
            """
            INSERT INTO meal_plan_entries (meal_date, meal_type, recipe_id, notes, user_id)
            VALUES (%s, %s, %s, %s, %s)
            """,
            params,
        )
        return int(cur.lastrowid)


def list_meal_plan_entries(
    start_date: str,
    end_date: str,
    user_id: Optional[int] = None,
    is_admin: bool = False,
) -> list[dict]:
    query = """
        SELECT
            m.id,
            m.meal_date,
            m.meal_type,
            m.notes,
            m.recipe_id,
            r.title AS recipe_title,
            r.category AS recipe_category
        FROM meal_plan_entries m
        JOIN recipes r ON r.id = m.recipe_id
        WHERE m.meal_date BETWEEN %s AND %s
    """
    params: list[object] = [start_date, end_date]
    if not is_admin:
        query += " AND m.user_id = %s"
        params.append(int(user_id or -1))

    query += """
        ORDER BY m.meal_date ASC,
                 CASE m.meal_type
                    WHEN 'Breakfast' THEN 1
                    WHEN 'Lunch' THEN 2
                    WHEN 'Dinner' THEN 3
                    WHEN 'Snack' THEN 4
                    ELSE 99
                 END ASC,
                 r.title ASC
    """

    with get_connection() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def delete_meal_plan_entry(entry_id: int, user_id: Optional[int] = None, is_admin: bool = False) -> bool:
    query = "DELETE FROM meal_plan_entries WHERE id = %s"
    params: list[object] = [int(entry_id)]
    if not is_admin:
        query += " AND user_id = %s"
        params.append(int(user_id or -1))

    with get_connection() as conn:
        cur = conn.execute(query, tuple(params))
    return int(cur.rowcount or 0) > 0
