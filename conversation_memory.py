import json
import re
import sqlite3
from pathlib import Path

import user_memory as um


PROJECT_DIRECTORY = Path(__file__).resolve().parent
DATA_DIRECTORY = PROJECT_DIRECTORY / "data"
DATABASE_PATH = DATA_DIRECTORY / "user_memory.db"


def get_connection():
    """Open the shared user-memory database."""
    DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


def init_database():
    """Create conversation-history tables."""
    um.init_database()

    with get_connection() as connection:
        connection.execute("PRAGMA journal_mode = WAL")

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_chat_settings (
                user_id INTEGER PRIMARY KEY,
                history_enabled INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id)
                    REFERENCES users(id)
                    ON DELETE CASCADE
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                answer_mode TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id)
                    REFERENCES users(id)
                    ON DELETE CASCADE
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL
                    CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id)
                    REFERENCES conversations(id)
                    ON DELETE CASCADE
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_conversations_user
            ON conversations(user_id, updated_at)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_messages_conversation
            ON conversation_messages(conversation_id, id)
            """
        )


def get_history_enabled(user_id):
    """Check whether the user enabled saved chat history."""
    init_database()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT history_enabled
            FROM user_chat_settings
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()

    return bool(row["history_enabled"]) if row else False


def set_history_enabled(user_id, enabled):
    """Enable or disable future conversation saving."""
    init_database()

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO user_chat_settings (
                user_id,
                history_enabled
            )
            VALUES (?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET
                history_enabled = excluded.history_enabled,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                user_id,
                int(bool(enabled)),
            ),
        )


def make_conversation_title(text, maximum_length=70):
    """Create a short title from the first user question."""
    title = re.sub(r"\s+", " ", text).strip()

    if not title:
        return "New conversation"

    if len(title) <= maximum_length:
        return title

    return title[: maximum_length - 1].rstrip() + "…"


def create_conversation(user_id, first_message, answer_mode):
    """Create a new saved conversation."""
    init_database()

    if not get_history_enabled(user_id):
        return None

    title = make_conversation_title(first_message)

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO conversations (
                user_id,
                title,
                answer_mode
            )
            VALUES (?, ?, ?)
            """,
            (
                user_id,
                title,
                answer_mode,
            ),
        )

    return cursor.lastrowid


def conversation_belongs_to_user(
    connection,
    conversation_id,
    user_id,
):
    """Confirm that a conversation belongs to the current user."""
    row = connection.execute(
        """
        SELECT id
        FROM conversations
        WHERE id = ?
          AND user_id = ?
        """,
        (
            conversation_id,
            user_id,
        ),
    ).fetchone()

    return row is not None


def add_message(
    user_id,
    conversation_id,
    role,
    content,
    evidence=None,
):
    """Save one user or assistant message."""
    init_database()

    if role not in {"user", "assistant"}:
        raise ValueError("Invalid conversation role.")

    if not conversation_id:
        return False

    evidence_json = json.dumps(
        evidence or [],
        ensure_ascii=False,
    )

    with get_connection() as connection:
        if not conversation_belongs_to_user(
            connection,
            conversation_id,
            user_id,
        ):
            return False

        connection.execute(
            """
            INSERT INTO conversation_messages (
                conversation_id,
                role,
                content,
                evidence_json
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                conversation_id,
                role,
                content,
                evidence_json,
            ),
        )

        connection.execute(
            """
            UPDATE conversations
            SET updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND user_id = ?
            """,
            (
                conversation_id,
                user_id,
            ),
        )

    return True


def list_conversations(user_id, limit=50):
    """List a user's saved conversations."""
    init_database()

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                title,
                answer_mode,
                created_at,
                updated_at
            FROM conversations
            WHERE user_id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (
                user_id,
                limit,
            ),
        ).fetchall()

    return [dict(row) for row in rows]


def load_conversation(user_id, conversation_id):
    """Load one conversation after checking ownership."""
    init_database()

    with get_connection() as connection:
        conversation = connection.execute(
            """
            SELECT
                id,
                title,
                answer_mode,
                created_at,
                updated_at
            FROM conversations
            WHERE id = ?
              AND user_id = ?
            """,
            (
                conversation_id,
                user_id,
            ),
        ).fetchone()

        if conversation is None:
            return None

        rows = connection.execute(
            """
            SELECT
                role,
                content,
                evidence_json,
                created_at
            FROM conversation_messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            """,
            (conversation_id,),
        ).fetchall()

    messages = []

    for row in rows:
        try:
            evidence = json.loads(
                row["evidence_json"] or "[]"
            )
        except json.JSONDecodeError:
            evidence = []

        messages.append(
            {
                "role": row["role"],
                "content": row["content"],
                "evidence": evidence,
                "created_at": row["created_at"],
            }
        )

    return {
        "conversation": dict(conversation),
        "messages": messages,
    }


def delete_conversation(user_id, conversation_id):
    """Delete one conversation belonging to the user."""
    init_database()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            DELETE FROM conversations
            WHERE id = ?
              AND user_id = ?
            """,
            (
                conversation_id,
                user_id,
            ),
        )

    return cursor.rowcount > 0


def delete_all_conversations(user_id):
    """Delete all saved conversations for one user."""
    init_database()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            DELETE FROM conversations
            WHERE user_id = ?
            """,
            (user_id,),
        )

    return cursor.rowcount


def saved_conversation_count(user_id):
    """Count the user's saved conversations."""
    init_database()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM conversations
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()

    return row["total"]