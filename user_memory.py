import hashlib
import hmac
import os
import re
import sqlite3
from pathlib import Path


PROJECT_DIRECTORY = Path(__file__).resolve().parent
DATA_DIRECTORY = PROJECT_DIRECTORY / "data"
DATABASE_PATH = DATA_DIRECTORY / "user_memory.db"

PBKDF2_ITERATIONS = 600_000


def get_connection():
    """Open the user-memory database."""
    DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


def init_database():
    """Create user and profile tables."""
    with get_connection() as connection:
        connection.execute("PRAGMA journal_mode = WAL")

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name TEXT NOT NULL,
                password_hash BLOB NOT NULL,
                password_salt BLOB NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id INTEGER PRIMARY KEY,
                memory_enabled INTEGER NOT NULL DEFAULT 0,
                study_level TEXT NOT NULL DEFAULT '',
                discipline TEXT NOT NULL DEFAULT '',
                preferred_language TEXT NOT NULL DEFAULT '',
                citation_style TEXT NOT NULL DEFAULT '',
                research_topic TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id)
                    REFERENCES users(id)
                    ON DELETE CASCADE
            )
            """
        )


def normalize_username(username):
    """Create a consistent lowercase username."""
    return username.strip().lower()


def validate_username(username):
    """
    Usernames may contain letters, numbers, periods,
    underscores and hyphens.
    """
    return bool(
        re.fullmatch(
            r"[a-zA-Z0-9_.-]{3,30}",
            username,
        )
    )


def hash_password(password, salt):
    """Create a secure password hash."""
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )


def create_user(username, display_name, password):
    """Register a new local prototype user."""
    init_database()

    username = normalize_username(username)
    display_name = display_name.strip()

    if not validate_username(username):
        raise ValueError(
            "Username must contain 3–30 letters, numbers, "
            "periods, underscores or hyphens."
        )

    if not display_name:
        raise ValueError("Display name is required.")

    if len(display_name) > 80:
        raise ValueError(
            "Display name must not exceed 80 characters."
        )

    if len(password) < 8:
        raise ValueError(
            "Password must contain at least 8 characters."
        )

    salt = os.urandom(16)
    password_hash = hash_password(password, salt)

    try:
        with get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO users (
                    username,
                    display_name,
                    password_hash,
                    password_salt
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    username,
                    display_name,
                    password_hash,
                    salt,
                ),
            )

            user_id = cursor.lastrowid

            connection.execute(
                """
                INSERT INTO user_profiles (user_id)
                VALUES (?)
                """,
                (user_id,),
            )

    except sqlite3.IntegrityError as error:
        raise ValueError(
            "That username is already registered."
        ) from error

    return {
        "id": user_id,
        "username": username,
        "display_name": display_name,
    }


def authenticate_user(username, password):
    """Verify a username and password."""
    init_database()

    username = normalize_username(username)

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                id,
                username,
                display_name,
                password_hash,
                password_salt
            FROM users
            WHERE username = ?
            """,
            (username,),
        ).fetchone()

    if row is None:
        return None

    attempted_hash = hash_password(
        password,
        row["password_salt"],
    )

    if not hmac.compare_digest(
        attempted_hash,
        row["password_hash"],
    ):
        return None

    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
    }


def get_profile(user_id):
    """Return the user's saved personalisation settings."""
    init_database()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                users.id,
                users.username,
                users.display_name,
                user_profiles.memory_enabled,
                user_profiles.study_level,
                user_profiles.discipline,
                user_profiles.preferred_language,
                user_profiles.citation_style,
                user_profiles.research_topic,
                user_profiles.updated_at
            FROM users
            JOIN user_profiles
                ON users.id = user_profiles.user_id
            WHERE users.id = ?
            """,
            (user_id,),
        ).fetchone()

    return dict(row) if row else None


def update_profile(
    user_id,
    memory_enabled,
    study_level,
    discipline,
    preferred_language,
    citation_style,
    research_topic,
):
    """Save consent-based user preferences."""
    init_database()

    values = {
        "study_level": study_level.strip()[:100],
        "discipline": discipline.strip()[:150],
        "preferred_language": (
            preferred_language.strip()[:80]
        ),
        "citation_style": citation_style.strip()[:80],
        "research_topic": research_topic.strip()[:500],
    }

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE user_profiles
            SET
                memory_enabled = ?,
                study_level = ?,
                discipline = ?,
                preferred_language = ?,
                citation_style = ?,
                research_topic = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
            """,
            (
                int(bool(memory_enabled)),
                values["study_level"],
                values["discipline"],
                values["preferred_language"],
                values["citation_style"],
                values["research_topic"],
                user_id,
            ),
        )


def clear_profile_memory(user_id):
    """Remove saved preferences but retain the account."""
    init_database()

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE user_profiles
            SET
                memory_enabled = 0,
                study_level = '',
                discipline = '',
                preferred_language = '',
                citation_style = '',
                research_topic = '',
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
            """,
            (user_id,),
        )


def delete_account(user_id):
    """Permanently delete a user and their profile."""
    init_database()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            DELETE FROM users
            WHERE id = ?
            """,
            (user_id,),
        )

    return cursor.rowcount > 0


def user_count():
    """Return the number of registered users."""
    init_database()

    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM users"
        ).fetchone()

    return row["total"]