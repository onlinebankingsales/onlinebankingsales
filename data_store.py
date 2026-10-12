import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DATABASE_PATH = Path(
    os.environ.get("DATA_DB_PATH", Path(__file__).with_name("records.db"))
).expanduser()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
SUPER_ADMIN_USERNAME = "352247"
PASSWORD_ITERATIONS = 600_000
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]{3,32}\Z")
DUMMY_SALT = b"streamlit-field-app-login-salt"


@contextmanager
def _connection():
    if DATABASE_URL:
        import psycopg
        from psycopg.rows import dict_row

        raw_connection = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        connection = _PostgresConnection(raw_connection)
    else:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        raw_connection = sqlite3.connect(DATABASE_PATH, timeout=30)
        raw_connection.row_factory = sqlite3.Row
        raw_connection.execute("PRAGMA foreign_keys = ON")
        connection = raw_connection
    try:
        yield connection
        raw_connection.commit()
    except Exception:
        raw_connection.rollback()
        raise
    finally:
        raw_connection.close()


class _PostgresConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, parameters=()):
        return self.connection.execute(query.replace("?", "%s"), parameters)


def _password_digest(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )


def _effective_role(user):
    if user["username"].casefold() == SUPER_ADMIN_USERNAME.casefold():
        return "super_admin"
    return user["role"]


def _insert_user(
    connection,
    username,
    employee_name,
    password,
    role,
    created_by_user_id=None,
):
    salt = secrets.token_bytes(16)
    password_hash = _password_digest(password, salt)
    cursor = connection.execute(
        """
        INSERT INTO users
            (username, employee_name, password_salt, password_hash, role,
             created_at, created_by_user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            username,
            employee_name,
            salt,
            password_hash,
            role,
            datetime.now().isoformat(timespec="seconds"),
            created_by_user_id,
        ),
    )
    return cursor.fetchone()["id"]


def _initialize_sqlite_schema(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL COLLATE NOCASE UNIQUE,
            employee_name TEXT NOT NULL,
            password_salt BLOB NOT NULL,
            password_hash BLOB NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
            created_at TEXT NOT NULL,
            created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            admin_owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            employee_name TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            logged_time TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            contact_number TEXT NOT NULL,
            acres REAL NOT NULL CHECK (acres >= 0),
            village TEXT NOT NULL,
            mandal TEXT NOT NULL,
            district TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS records_user_date_idx
            ON records(user_id, entry_date, id);
        """
    )
    user_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(users)")
    }
    if "created_by_user_id" not in user_columns:
        connection.execute(
            """ALTER TABLE users ADD COLUMN created_by_user_id
               INTEGER REFERENCES users(id) ON DELETE SET NULL"""
        )

    record_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(records)")
    }
    if "admin_owner_id" not in record_columns:
        connection.execute(
            """ALTER TABLE records ADD COLUMN admin_owner_id
               INTEGER REFERENCES users(id) ON DELETE SET NULL"""
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS users_creator_idx ON users(created_by_user_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS records_admin_owner_idx "
        "ON records(admin_owner_id, entry_date, id)"
    )


def _initialize_postgres_schema(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            employee_name TEXT NOT NULL,
            password_salt BYTEA NOT NULL,
            password_hash BYTEA NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
            created_at TEXT NOT NULL,
            created_by_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS records (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
            admin_owner_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
            employee_name TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            logged_time TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            contact_number TEXT NOT NULL,
            acres DOUBLE PRECISION NOT NULL CHECK (acres >= 0),
            village TEXT NOT NULL,
            mandal TEXT NOT NULL,
            district TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS created_by_user_id
           BIGINT REFERENCES users(id) ON DELETE SET NULL"""
    )
    connection.execute(
        """ALTER TABLE records ADD COLUMN IF NOT EXISTS admin_owner_id
           BIGINT REFERENCES users(id) ON DELETE SET NULL"""
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower_idx "
        "ON users (LOWER(username))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS users_creator_idx ON users(created_by_user_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS records_user_date_idx "
        "ON records(user_id, entry_date, id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS records_admin_owner_idx "
        "ON records(admin_owner_id, entry_date, id)"
    )


def _ensure_super_admin(connection):
    super_admin = connection.execute(
        "SELECT id FROM users WHERE LOWER(username) = LOWER(?)",
        (SUPER_ADMIN_USERNAME,),
    ).fetchone()
    if super_admin:
        super_admin_id = super_admin["id"]
        connection.execute(
            "UPDATE users SET role = 'admin' WHERE id = ?", (super_admin_id,)
        )
        connection.execute(
            """UPDATE users SET created_by_user_id = ?
               WHERE id != ? AND created_by_user_id IS NULL""",
            (super_admin_id, super_admin_id),
        )
        connection.execute(
            "UPDATE records SET admin_owner_id = ? WHERE admin_owner_id IS NULL",
            (super_admin_id,),
        )
        return True

    username = os.environ.get("INITIAL_ADMIN_USERNAME", "").strip()
    password = os.environ.get("INITIAL_ADMIN_PASSWORD", "")
    if username.casefold() != SUPER_ADMIN_USERNAME.casefold():
        return False
    if not USERNAME_PATTERN.fullmatch(username) or len(password) < 8:
        return False

    super_admin_id = _insert_user(
        connection, username, "Administrator", password, "admin"
    )
    connection.execute(
        "UPDATE users SET created_by_user_id = ? WHERE id != ?",
        (super_admin_id, super_admin_id),
    )
    connection.execute(
        "UPDATE records SET admin_owner_id = ? WHERE admin_owner_id IS NULL",
        (super_admin_id,),
    )
    return True


def initialize_database():
    with _connection() as connection:
        if DATABASE_URL:
            _initialize_postgres_schema(connection)
        else:
            _initialize_sqlite_schema(connection)
        return _ensure_super_admin(connection)


def authenticate(username, password):
    normalized_username = username.strip()
    with _connection() as connection:
        user = connection.execute(
            "SELECT * FROM users WHERE LOWER(username) = LOWER(?)",
            (normalized_username,),
        ).fetchone()

    salt = user["password_salt"] if user else DUMMY_SALT
    expected_hash = user["password_hash"] if user else bytes(32)
    actual_hash = _password_digest(password, salt)
    if user and hmac.compare_digest(actual_hash, expected_hash):
        public_user = {
            key: user[key] for key in ("id", "username", "employee_name", "role")
        }
        public_user["role"] = _effective_role(user)
        return public_user
    return None


def get_user(user_id):
    with _connection() as connection:
        user = connection.execute(
            "SELECT id, username, employee_name, role FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if not user:
        return None
    public_user = dict(user)
    public_user["role"] = _effective_role(user)
    return public_user


def create_account(requester_id, username, employee_name, password, role="user"):
    username = username.strip()
    employee_name = employee_name.strip()
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError("Username must be 3-32 letters, numbers, dots, dashes, or underscores.")
    if username.casefold() == SUPER_ADMIN_USERNAME.casefold():
        raise ValueError("That username is reserved for the super administrator.")
    if not employee_name:
        raise ValueError("Employee name is required.")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    if role not in ("admin", "user"):
        raise ValueError("Choose a valid account role.")

    with _connection() as connection:
        requester = connection.execute(
            "SELECT id, username, role FROM users WHERE id = ?", (requester_id,)
        ).fetchone()
        if not requester:
            raise ValueError("The administrator account is no longer available.")
        requester_role = _effective_role(requester)
        if requester_role != "super_admin" and requester_role != "admin":
            raise ValueError("Only administrators can create accounts.")
        if requester_role != "super_admin" and role != "user":
            raise ValueError("Only the super administrator can create administrators.")
        try:
            user_id = _insert_user(
                connection,
                username,
                employee_name,
                password,
                role,
                requester_id,
            )
        except Exception as error:
            if isinstance(error, sqlite3.IntegrityError) or getattr(
                error, "sqlstate", None
            ) == "23505":
                raise ValueError("That username is already in use.") from error
            raise
    return user_id


def list_users(requester_id):
    with _connection() as connection:
        requester = connection.execute(
            "SELECT id, username, role FROM users WHERE id = ?", (requester_id,)
        ).fetchone()
        if not requester:
            raise ValueError("The account is no longer available.")
        requester_role = _effective_role(requester)
        if requester_role == "super_admin":
            where_clause = ""
            parameters = ()
        elif requester_role == "admin":
            where_clause = "WHERE account.created_by_user_id = ? AND account.role = 'user'"
            parameters = (requester_id,)
        else:
            raise ValueError("Only administrators can manage accounts.")
        users = connection.execute(
            f"""
                 SELECT account.id, account.username, account.employee_name, account.role,
                     account.created_at, account.created_by_user_id,
                   creator.username AS created_by_username
                 FROM users AS account
                 LEFT JOIN users AS creator ON creator.id = account.created_by_user_id
            {where_clause}
                 ORDER BY account.role DESC, LOWER(account.username)
            """,
            parameters,
        ).fetchall()
    public_users = []
    for user in users:
        public_user = dict(user)
        public_user["role"] = _effective_role(user)
        public_users.append(public_user)
    return public_users


def delete_user(user_id, requester_id):
    if user_id == requester_id:
        raise ValueError("You cannot delete the account currently in use.")

    with _connection() as connection:
        requester = connection.execute(
            "SELECT id, username, role FROM users WHERE id = ?", (requester_id,)
        ).fetchone()
        if not requester:
            raise ValueError("The administrator account is no longer available.")
        user = connection.execute(
            "SELECT id, username, role, created_by_user_id FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not user:
            raise ValueError("That account no longer exists.")
        requester_role = _effective_role(requester)
        if requester_role == "super_admin":
            if user["username"].casefold() == SUPER_ADMIN_USERNAME.casefold():
                raise ValueError("The super administrator account cannot be deleted.")
        elif not (
            requester_role == "admin"
            and user["role"] == "user"
            and user["created_by_user_id"] == requester_id
        ):
            raise ValueError("You can only delete employee accounts you created.")

        if user["role"] == "admin":
            admin_count = connection.execute(
                "SELECT COUNT(*) AS account_count FROM users WHERE role = 'admin'"
            ).fetchone()["account_count"]
            if admin_count <= 1:
                raise ValueError("The last administrator account cannot be deleted.")
            connection.execute(
                "UPDATE users SET created_by_user_id = ? WHERE created_by_user_id = ?",
                (requester_id, user_id),
            )
            connection.execute(
                "UPDATE records SET admin_owner_id = ? WHERE admin_owner_id = ?",
                (requester_id, user_id),
            )
        connection.execute("DELETE FROM users WHERE id = ?", (user_id,))


def add_record(
    user_id,
    entry_date,
    logged_time,
    customer_name,
    contact_number,
    acres,
    village,
    mandal,
    district,
):
    with _connection() as connection:
        user = connection.execute(
            """SELECT employee_name, created_by_user_id
               FROM users WHERE id = ? AND role = 'user'""",
            (user_id,),
        ).fetchone()
        if not user:
            raise ValueError("The employee account is no longer available.")
        connection.execute(
            """
            INSERT INTO records (
                user_id, admin_owner_id, employee_name, entry_date, logged_time,
                customer_name, contact_number, acres, village, mandal, district
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                user["created_by_user_id"],
                user["employee_name"],
                entry_date,
                logged_time,
                customer_name,
                contact_number,
                acres,
                village,
                mandal,
                district,
            ),
        )


RECORD_COLUMNS = """
    entry_date AS "Entry Date",
    logged_time AS "Logged Time",
    employee_name AS "Employee Name",
    customer_name AS "Customer Name",
    contact_number AS "Contact Number",
    acres AS "Acres",
    village AS "Village",
    mandal AS "Mandal",
    district AS "District"
"""


def get_user_records(user_id):
    with _connection() as connection:
        records = connection.execute(
            f"SELECT {RECORD_COLUMNS} FROM records WHERE user_id = ? ORDER BY entry_date DESC, id DESC",
            (user_id,),
        ).fetchall()
    return [dict(record) for record in records]


def get_admin_records(admin_id):
    with _connection() as connection:
        admin = connection.execute(
            "SELECT id, username, role FROM users WHERE id = ?", (admin_id,)
        ).fetchone()
        if not admin:
            raise ValueError("The administrator account is no longer available.")
        if _effective_role(admin) == "super_admin":
            query = (
                f"SELECT {RECORD_COLUMNS} FROM records "
                "ORDER BY entry_date DESC, id DESC"
            )
            parameters = ()
        elif admin["role"] == "admin":
            query = (
                f"SELECT {RECORD_COLUMNS} FROM records "
                "WHERE admin_owner_id = ? ORDER BY entry_date DESC, id DESC"
            )
            parameters = (admin_id,)
        else:
            raise ValueError("Only administrators can view team records.")
        records = connection.execute(
            query, parameters
        ).fetchall()
    return [dict(record) for record in records]


def migrate_sqlite_to_postgres(source_path=None):
    if not DATABASE_URL:
        raise RuntimeError("Set DATABASE_URL before migrating to PostgreSQL.")

    source_path = Path(source_path or DATABASE_PATH).expanduser()
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {source_path}")

    with sqlite3.connect(source_path) as source:
        source.row_factory = sqlite3.Row
        user_columns = {
            row["name"] for row in source.execute("PRAGMA table_info(users)")
        }
        record_columns = {
            row["name"] for row in source.execute("PRAGMA table_info(records)")
        }
        users = source.execute("SELECT * FROM users ORDER BY id").fetchall()
        records = source.execute("SELECT * FROM records ORDER BY id").fetchall()

    super_admin = next(
        (
            user
            for user in users
            if user["username"].casefold() == SUPER_ADMIN_USERNAME.casefold()
        ),
        None,
    )
    if not super_admin:
        raise ValueError("The SQLite database has no super administrator account.")
    super_admin_id = super_admin["id"]

    with _connection() as connection:
        _initialize_postgres_schema(connection)
        target_user_count = connection.execute(
            "SELECT COUNT(*) AS total FROM users"
        ).fetchone()["total"]
        target_record_count = connection.execute(
            "SELECT COUNT(*) AS total FROM records"
        ).fetchone()["total"]
        if target_record_count or target_user_count > 1:
            raise ValueError("The PostgreSQL database must be empty before migration.")
        if target_user_count == 1:
            bootstrap_user = connection.execute(
                "SELECT username FROM users"
            ).fetchone()
            if bootstrap_user["username"].casefold() != SUPER_ADMIN_USERNAME.casefold():
                raise ValueError(
                    "The PostgreSQL database only supports replacing its initial super-admin account."
                )
            connection.execute("DELETE FROM users")

        for user in users:
            is_super_admin = (
                user["username"].casefold() == SUPER_ADMIN_USERNAME.casefold()
            )
            created_by_user_id = (
                user["created_by_user_id"]
                if "created_by_user_id" in user_columns
                else None if is_super_admin else super_admin_id
            )
            connection.execute(
                """
                INSERT INTO users (
                    id, username, employee_name, password_salt, password_hash,
                    role, created_at, created_by_user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user["id"],
                    user["username"],
                    user["employee_name"],
                    user["password_salt"],
                    user["password_hash"],
                    "admin" if is_super_admin else user["role"],
                    user["created_at"],
                    created_by_user_id,
                ),
            )

        for record in records:
            admin_owner_id = (
                record["admin_owner_id"]
                if "admin_owner_id" in record_columns
                else super_admin_id
            )
            connection.execute(
                """
                INSERT INTO records (
                    id, user_id, admin_owner_id, employee_name, entry_date,
                    logged_time, customer_name, contact_number, acres, village,
                    mandal, district
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["user_id"],
                    admin_owner_id,
                    record["employee_name"],
                    record["entry_date"],
                    record["logged_time"],
                    record["customer_name"],
                    record["contact_number"],
                    record["acres"],
                    record["village"],
                    record["mandal"],
                    record["district"],
                ),
            )

        connection.execute(
            """SELECT setval(pg_get_serial_sequence('users', 'id'),
               COALESCE(MAX(id), 1), COUNT(*) > 0) FROM users"""
        )
        connection.execute(
            """SELECT setval(pg_get_serial_sequence('records', 'id'),
               COALESCE(MAX(id), 1), COUNT(*) > 0) FROM records"""
        )

    return {"users": len(users), "records": len(records)}
