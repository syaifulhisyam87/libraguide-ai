import re
import sqlite3
from pathlib import Path

import user_memory as um


PROJECT_DIRECTORY = Path(__file__).resolve().parent
DATA_DIRECTORY = PROJECT_DIRECTORY / "data"
DATABASE_PATH = DATA_DIRECTORY / "user_memory.db"

VALID_CATEGORIES = (
    "Incorrect answer",
    "Missing information",
    "Outdated information",
    "Citation problem",
    "Unclear answer",
    "Other",
)

VALID_STATUSES = (
    "pending",
    "approved",
    "rejected",
)


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
    """Create the moderated feedback table."""
    um.init_database()

    with get_connection() as connection:
        connection.execute("PRAGMA journal_mode = WAL")

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                category TEXT NOT NULL,
                question TEXT NOT NULL,
                assistant_answer TEXT NOT NULL,
                suggestion TEXT NOT NULL,
                supporting_source TEXT NOT NULL DEFAULT '',
                answer_mode TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (
                        status IN (
                            'pending',
                            'approved',
                            'rejected'
                        )
                    ),
                admin_notes TEXT NOT NULL DEFAULT '',
                submitted_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TEXT,
                FOREIGN KEY (user_id)
                    REFERENCES users(id)
                    ON DELETE SET NULL
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_feedback_status
            ON feedback_submissions(status, submitted_at)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_feedback_user
            ON feedback_submissions(user_id, submitted_at)
            """
        )


def clean_text(value, maximum_length):
    """Normalize text and enforce a storage limit."""
    value = str(value or "").strip()
    value = re.sub(r"\r\n?", "\n", value)

    if len(value) > maximum_length:
        value = value[:maximum_length].rstrip()

    return value


def submit_feedback(
    user_id,
    category,
    question,
    assistant_answer,
    suggestion,
    supporting_source="",
    answer_mode="",
):
    """Submit feedback for administrator review."""
    init_database()

    if category not in VALID_CATEGORIES:
        raise ValueError("Select a valid feedback category.")

    question = clean_text(question, 4_000)
    assistant_answer = clean_text(
        assistant_answer,
        12_000,
    )
    suggestion = clean_text(suggestion, 6_000)
    supporting_source = clean_text(
        supporting_source,
        2_000,
    )
    answer_mode = clean_text(answer_mode, 100)

    if not question:
        raise ValueError(
            "A question or issue description is required."
        )

    if not suggestion:
        raise ValueError(
            "Describe what should be corrected or improved."
        )

    try:
        with get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO feedback_submissions (
                    user_id,
                    category,
                    question,
                    assistant_answer,
                    suggestion,
                    supporting_source,
                    answer_mode
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    category,
                    question,
                    assistant_answer,
                    suggestion,
                    supporting_source,
                    answer_mode,
                ),
            )

    except sqlite3.IntegrityError as error:
        raise ValueError(
            "The signed-in user account could not be verified."
        ) from error

    return cursor.lastrowid


def list_feedback(status="pending", limit=100):
    """List feedback for the administrator review queue."""
    init_database()

    if status is not None and status not in VALID_STATUSES:
        raise ValueError("Invalid feedback status.")

    limit = max(1, min(int(limit), 500))

    query = """
        SELECT
            feedback_submissions.id,
            feedback_submissions.user_id,
            users.username,
            users.display_name,
            feedback_submissions.category,
            feedback_submissions.question,
            feedback_submissions.assistant_answer,
            feedback_submissions.suggestion,
            feedback_submissions.supporting_source,
            feedback_submissions.answer_mode,
            feedback_submissions.status,
            feedback_submissions.admin_notes,
            feedback_submissions.submitted_at,
            feedback_submissions.reviewed_at
        FROM feedback_submissions
        LEFT JOIN users
            ON users.id = feedback_submissions.user_id
    """

    parameters = []

    if status is not None:
        query += " WHERE feedback_submissions.status = ?"
        parameters.append(status)

    query += """
        ORDER BY
            feedback_submissions.submitted_at DESC,
            feedback_submissions.id DESC
        LIMIT ?
    """

    parameters.append(limit)

    with get_connection() as connection:
        rows = connection.execute(
            query,
            parameters,
        ).fetchall()

    return [dict(row) for row in rows]


def get_feedback(feedback_id):
    """Return one feedback submission."""
    init_database()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                feedback_submissions.*,
                users.username,
                users.display_name
            FROM feedback_submissions
            LEFT JOIN users
                ON users.id = feedback_submissions.user_id
            WHERE feedback_submissions.id = ?
            """,
            (feedback_id,),
        ).fetchone()

    return dict(row) if row else None


def review_feedback(
    feedback_id,
    status,
    admin_notes="",
):
    """Approve, reject or reopen a feedback submission."""
    init_database()

    if status not in VALID_STATUSES:
        raise ValueError("Invalid feedback status.")

    admin_notes = clean_text(admin_notes, 6_000)

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE feedback_submissions
            SET
                status = ?,
                admin_notes = ?,
                reviewed_at = CASE
                    WHEN ? = 'pending' THEN NULL
                    ELSE CURRENT_TIMESTAMP
                END
            WHERE id = ?
            """,
            (
                status,
                admin_notes,
                status,
                feedback_id,
            ),
        )

    return cursor.rowcount > 0


def list_user_feedback(user_id, limit=50):
    """List feedback submitted by one signed-in user."""
    init_database()

    limit = max(1, min(int(limit), 200))

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                category,
                question,
                suggestion,
                status,
                admin_notes,
                submitted_at,
                reviewed_at
            FROM feedback_submissions
            WHERE user_id = ?
            ORDER BY submitted_at DESC, id DESC
            LIMIT ?
            """,
            (
                user_id,
                limit,
            ),
        ).fetchall()

    return [dict(row) for row in rows]


def delete_pending_feedback(user_id, feedback_id):
    """Let a user withdraw feedback that is still pending."""
    init_database()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            DELETE FROM feedback_submissions
            WHERE id = ?
              AND user_id = ?
              AND status = 'pending'
            """,
            (
                feedback_id,
                user_id,
            ),
        )

    return cursor.rowcount > 0


def feedback_counts():
    """Return administrator queue totals by status."""
    init_database()

    counts = {
        "pending": 0,
        "approved": 0,
        "rejected": 0,
    }

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT status, COUNT(*) AS total
            FROM feedback_submissions
            GROUP BY status
            """
        ).fetchall()

    for row in rows:
        counts[row["status"]] = row["total"]

    return counts
