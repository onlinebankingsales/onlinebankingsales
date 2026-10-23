import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DATABASE_PATH = Path.home() / ".field_operations" / "records.db"
DATABASE_PATH = Path(
    os.environ.get("DATA_DB_PATH", str(DEFAULT_DATABASE_PATH))
).expanduser()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
SUPER_ADMIN_USERNAME = "352247"
PASSWORD_ITERATIONS = 600_000
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]{3,32}\Z")
DUMMY_SALT = b"streamlit-field-app-login-salt"
REMARK_OPTIONS = ("Interested", "Not interested")
APP_TIMEZONE = timezone(timedelta(hours=5, minutes=30), name="UTC+05:30")


@contextmanager
def _connection(write=False):
    if DATABASE_URL:
        import psycopg
        from psycopg.rows import dict_row

        raw_connection = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        connection = _PostgresConnection(raw_connection)
    else:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        if write:
            _backup_sqlite_database()
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


def _sqlite_table_counts(path):
    if not path.is_file():
        return 0, 0

    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        user_count = (
            db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if "users" in tables
            else 0
        )
        record_count = (
            db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            if "records" in tables
            else 0
        )
    return user_count, record_count


def _migrate_legacy_sqlite_path():
    if DATABASE_URL or os.environ.get("DATA_DB_PATH"):
        return

    legacy_path = Path(__file__).with_name("records.db")
    marker_path = DATABASE_PATH.with_name(
        f"{DATABASE_PATH.name}.legacy-imported"
    )
    if marker_path.exists():
        return
    if legacy_path.resolve() == DATABASE_PATH.resolve() or not legacy_path.is_file():
        return

    legacy_counts = _sqlite_table_counts(legacy_path)
    if legacy_counts == (0, 0):
        return

    current_counts = _sqlite_table_counts(DATABASE_PATH)
    if current_counts != (0, 0):
        raise RuntimeError(
            "Both the legacy and current SQLite databases contain accounts or "
            "records. Neither was changed. Set DATA_DB_PATH to the database "
            "containing the data you want to use, or merge the databases before "
            "starting the app."
        )

    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = DATABASE_PATH.with_name(
        f"{DATABASE_PATH.name}.migration-{secrets.token_hex(8)}"
    )
    try:
        with closing(sqlite3.connect(legacy_path)) as source:
            with closing(sqlite3.connect(temporary_path)) as destination:
                source.backup(destination)
                integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise RuntimeError(
                        f"Legacy SQLite database failed integrity check: {integrity}"
                    )
        os.replace(temporary_path, DATABASE_PATH)
        marker_path.write_text(
            str(legacy_path.resolve()),
            encoding="utf-8",
        )
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()
        raise


def _backup_sqlite_database():
    if DATABASE_URL or not DATABASE_PATH.is_file():
        return

    timestamp = datetime.now(APP_TIMEZONE).strftime("%Y%m%d-%H%M%S-%f")
    backup_path = DATABASE_PATH.with_name(
        f"{DATABASE_PATH.name}.backup-{timestamp}.sqlite"
    )
    try:
        with closing(sqlite3.connect(DATABASE_PATH)) as source:
            with closing(sqlite3.connect(backup_path)) as backup:
                source.backup(backup)
                integrity = backup.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise RuntimeError(
                        f"SQLite backup failed integrity check: {integrity}"
                    )
    except Exception:
        if backup_path.exists():
            backup_path.unlink()
        raise


class _PostgresConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, parameters=()):
        return self.connection.execute(query.replace("?", "%s"), parameters)


def _password_digest(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )


def _set_user_password(connection, user_id, new_password):
    salt = secrets.token_bytes(16)
    password_hash = _password_digest(new_password, salt)
    connection.execute(
        "UPDATE users SET password_salt = ?, password_hash = ? WHERE id = ?",
        (salt, password_hash, user_id),
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
            datetime.now(APP_TIMEZONE).isoformat(timespec="seconds"),
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
            created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
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
            district TEXT NOT NULL,
            remarks TEXT NOT NULL DEFAULT 'Not interested'
            CHECK (remarks IN ('Interested', 'Not interested'))
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
    if "is_active" not in user_columns:
        connection.execute(
            "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
        )
        connection.execute(
            "UPDATE users SET is_active = 1 WHERE is_active IS NULL"
        )

    record_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(records)")
    }
    if "admin_owner_id" not in record_columns:
        connection.execute(
            """ALTER TABLE records ADD COLUMN admin_owner_id
               INTEGER REFERENCES users(id) ON DELETE SET NULL"""
        )
    if "remarks" not in record_columns:
        connection.execute(
            "ALTER TABLE records ADD COLUMN remarks TEXT NOT NULL DEFAULT 'Not interested'"
        )
        connection.execute(
            "UPDATE records SET remarks = 'Not interested' WHERE remarks IS NULL"
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
            created_by_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
            is_active SMALLINT NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
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
            district TEXT NOT NULL,
            remarks TEXT NOT NULL DEFAULT 'Not interested'
        )
        """
    )
    connection.execute(
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS created_by_user_id
           BIGINT REFERENCES users(id) ON DELETE SET NULL"""
    )
    connection.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active SMALLINT NOT NULL DEFAULT 1"
    )
    connection.execute(
        "UPDATE users SET is_active = 1 WHERE is_active IS NULL"
    )
    connection.execute(
        """ALTER TABLE records ADD COLUMN IF NOT EXISTS admin_owner_id
           BIGINT REFERENCES users(id) ON DELETE SET NULL"""
    )
    connection.execute(
        "ALTER TABLE records ADD COLUMN IF NOT EXISTS remarks TEXT NOT NULL DEFAULT 'Not interested'"
    )
    connection.execute(
        "UPDATE records SET remarks = 'Not interested' WHERE remarks IS NULL"
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
        "SELECT id FROM users WHERE LOWER(username) = LOWER(?) AND is_active = 1",
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
    _migrate_legacy_sqlite_path()
    with _connection() as connection:
        if DATABASE_URL:
            _initialize_postgres_schema(connection)
        else:
            _initialize_sqlite_schema(connection)
        return _ensure_super_admin(connection)


def change_own_password(user_id, current_password, new_password):
    if len(new_password) < 8:
        raise ValueError("Password must be at least 8 characters.")

    with _connection(write=True) as connection:
        user = connection.execute(
            "SELECT id, password_salt, password_hash FROM users WHERE id = ? AND is_active = 1",
            (user_id,),
        ).fetchone()
        if not user:
            raise ValueError("The account is no longer available.")
        current_hash = _password_digest(current_password, user["password_salt"])
        if not hmac.compare_digest(current_hash, user["password_hash"]):
            raise ValueError("The current password is incorrect.")
        _set_user_password(connection, user_id, new_password)


def reset_user_password(requester_id, target_user_id, new_password):
    if len(new_password) < 8:
        raise ValueError("Password must be at least 8 characters.")

    with _connection(write=True) as connection:
        requester = connection.execute(
            "SELECT id, username, role, created_by_user_id FROM users WHERE id = ? AND is_active = 1",
            (requester_id,),
        ).fetchone()
        if not requester:
            raise ValueError("The administrator account is no longer available.")
        target = connection.execute(
            "SELECT id, username, role, created_by_user_id, is_active FROM users WHERE id = ?",
            (target_user_id,),
        ).fetchone()
        if not target:
            raise ValueError("That account no longer exists.")
        if target["is_active"] == 0:
            raise ValueError("That account is already inactive.")

        requester_role = _effective_role(requester)
        if requester_role == "super_admin":
            if target["username"].casefold() == SUPER_ADMIN_USERNAME.casefold():
                raise ValueError("The super administrator password cannot be reset here.")
        elif requester_role == "admin":
            if target["role"] != "user" or target["created_by_user_id"] != requester_id:
                raise ValueError("You can only reset employee passwords you created.")
        else:
            raise ValueError("Only administrators can reset passwords.")

        _set_user_password(connection, target_user_id, new_password)


def export_sqlite_database():
    if DATABASE_URL:
        raise RuntimeError("SQLite backups are only available when using SQLite.")
    if not DATABASE_PATH.is_file():
        raise FileNotFoundError(f"SQLite database not found: {DATABASE_PATH}")

    source_uri = f"{DATABASE_PATH.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as source:
        with closing(sqlite3.connect(":memory:")) as backup:
            source.backup(backup)
            return backup.serialize()


def authenticate(username, password):
    normalized_username = username.strip()
    with _connection() as connection:
        user = connection.execute(
            "SELECT * FROM users WHERE LOWER(username) = LOWER(?) AND is_active = 1",
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
            "SELECT id, username, employee_name, role FROM users WHERE id = ? AND is_active = 1",
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

    with _connection(write=True) as connection:
        requester = connection.execute(
            "SELECT id, username, role FROM users WHERE id = ? AND is_active = 1",
            (requester_id,),
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
            "SELECT id, username, role FROM users WHERE id = ? AND is_active = 1",
            (requester_id,),
        ).fetchone()
        if not requester:
            raise ValueError("The account is no longer available.")
        requester_role = _effective_role(requester)
        if requester_role == "super_admin":
            where_clause = "WHERE account.is_active = 1"
            parameters = ()
        elif requester_role == "admin":
            where_clause = "WHERE account.created_by_user_id = ? AND account.role = 'user' AND account.is_active = 1"
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

    with _connection(write=True) as connection:
        requester = connection.execute(
            "SELECT id, username, role FROM users WHERE id = ? AND is_active = 1",
            (requester_id,),
        ).fetchone()
        if not requester:
            raise ValueError("The administrator account is no longer available.")
        user = connection.execute(
            "SELECT id, username, role, created_by_user_id, is_active FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not user:
            raise ValueError("That account no longer exists.")
        if user["is_active"] == 0:
            raise ValueError("That account is already inactive.")
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
                "SELECT COUNT(*) AS account_count FROM users WHERE role = 'admin' AND is_active = 1"
            ).fetchone()["account_count"]
            if admin_count <= 1:
                raise ValueError("The last administrator account cannot be deleted.")
            connection.execute(
                "UPDATE users SET created_by_user_id = ? WHERE created_by_user_id = ? AND is_active = 1",
                (requester_id, user_id),
            )
            connection.execute(
                "UPDATE records SET admin_owner_id = ? WHERE admin_owner_id = ?",
                (requester_id, user_id),
            )
        connection.execute(
            "UPDATE users SET is_active = 0 WHERE id = ?",
            (user_id,),
        )


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
    remarks,
):
    if remarks not in REMARK_OPTIONS:
        raise ValueError("Please select a valid customer remark.")
    with _connection(write=True) as connection:
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
                customer_name, contact_number, acres, village, mandal, district,
                remarks
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                remarks,
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
    district AS "District",
    remarks AS "Remarks"
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
            "SELECT id, username, role FROM users WHERE id = ? AND is_active = 1",
            (admin_id,),
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
                    role, created_at, created_by_user_id, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    user["is_active"] if "is_active" in user_columns else 1,
                ),
            )

        for record in records:
            admin_owner_id = (
                record["admin_owner_id"]
                if "admin_owner_id" in record_columns
                else super_admin_id
            )
            remark = (
                record["remarks"]
                if "remarks" in record_columns
                else "Not interested"
            )
            if remark not in REMARK_OPTIONS:
                remark = "Not interested"
            connection.execute(
                """
                INSERT INTO records (
                    id, user_id, admin_owner_id, employee_name, entry_date,
                    logged_time, customer_name, contact_number, acres, village,
                    mandal, district, remarks
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    remark,
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
