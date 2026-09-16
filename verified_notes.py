import re
import sqlite3

import numpy as np

import feedback_memory as fm
import knowledge_base as kb


DATABASE_PATH = kb.DATABASE_PATH


def get_connection():
    """Open the shared LibraGuide knowledge database."""
    kb.DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


def init_database():
    """Create the verified knowledge-note table."""
    kb.init_database()

    with get_connection() as connection:
        connection.execute("PRAGMA journal_mode = WAL")

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS verified_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feedback_id INTEGER NOT NULL UNIQUE,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                source_reference TEXT NOT NULL,
                embedding BLOB NOT NULL,
                embedding_dimensions INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
                    CHECK (active IN (0, 1)),
                created_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_verified_notes_active
            ON verified_notes(active, updated_at)
            """
        )


def clean_text(value, maximum_length):
    """Normalize administrator-provided note text."""
    value = str(value or "").strip()
    value = re.sub(r"\r\n?", "\n", value)

    if len(value) > maximum_length:
        value = value[:maximum_length].rstrip()

    return value


def publication_text(title, content, source_reference):
    """Create the text used to generate the note embedding."""
    return (
        f"Title: {title}\n"
        f"Verified guidance: {content}\n"
        f"Source reference: {source_reference}"
    )


def publish_note(
    feedback_id,
    title,
    content,
    source_reference,
):
    """Publish or update a note from approved feedback."""
    init_database()

    feedback = fm.get_feedback(feedback_id)

    if feedback is None:
        raise ValueError(
            "The linked feedback submission could not be found."
        )

    if feedback["status"] != "approved":
        raise ValueError(
            "Only approved feedback can become a verified note."
        )

    title = clean_text(title, 200)
    content = clean_text(content, 8_000)
    source_reference = clean_text(
        source_reference,
        2_000,
    )

    if not title:
        raise ValueError("A verified-note title is required.")

    if not content:
        raise ValueError(
            "Verified guidance content is required."
        )

    if not source_reference:
        raise ValueError(
            "Record the source used to verify this note."
        )

    embedding = kb.create_embeddings(
        [
            publication_text(
                title,
                content,
                source_reference,
            )
        ]
    )[0]

    embedding_blob = embedding.astype(
        np.float32
    ).tobytes()

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO verified_notes (
                feedback_id,
                title,
                content,
                source_reference,
                embedding,
                embedding_dimensions,
                active
            )
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(feedback_id)
            DO UPDATE SET
                title = excluded.title,
                content = excluded.content,
                source_reference = excluded.source_reference,
                embedding = excluded.embedding,
                embedding_dimensions = (
                    excluded.embedding_dimensions
                ),
                active = 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                feedback_id,
                title,
                content,
                source_reference,
                embedding_blob,
                int(embedding.shape[0]),
            ),
        )

        row = connection.execute(
            """
            SELECT id
            FROM verified_notes
            WHERE feedback_id = ?
            """,
            (feedback_id,),
        ).fetchone()

    return row["id"]


def get_note(note_id):
    """Return one verified note."""
    init_database()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                id,
                feedback_id,
                title,
                content,
                source_reference,
                active,
                created_at,
                updated_at
            FROM verified_notes
            WHERE id = ?
            """,
            (note_id,),
        ).fetchone()

    return dict(row) if row else None


def get_note_by_feedback(feedback_id):
    """Return the note linked to a feedback submission."""
    init_database()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                id,
                feedback_id,
                title,
                content,
                source_reference,
                active,
                created_at,
                updated_at
            FROM verified_notes
            WHERE feedback_id = ?
            """,
            (feedback_id,),
        ).fetchone()

    return dict(row) if row else None


def list_notes(include_inactive=True):
    """List verified notes for administrator management."""
    init_database()

    query = """
        SELECT
            id,
            feedback_id,
            title,
            content,
            source_reference,
            active,
            created_at,
            updated_at
        FROM verified_notes
    """

    if not include_inactive:
        query += " WHERE active = 1"

    query += " ORDER BY updated_at DESC, id DESC"

    with get_connection() as connection:
        rows = connection.execute(query).fetchall()

    return [dict(row) for row in rows]


def set_note_active(note_id, active):
    """Publish, reactivate or retire a verified note."""
    init_database()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE verified_notes
            SET
                active = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                int(bool(active)),
                note_id,
            ),
        )

    return cursor.rowcount > 0


def active_note_count():
    """Count notes currently available for retrieval."""
    init_database()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM verified_notes
            WHERE active = 1
            """
        ).fetchone()

    return row["total"]


def search_verified_notes(question, top_k=5):
    """Retrieve verified notes using cosine similarity."""
    init_database()

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                title,
                content,
                source_reference,
                embedding,
                embedding_dimensions
            FROM verified_notes
            WHERE active = 1
            ORDER BY id
            """
        ).fetchall()

    if not rows:
        return []

    question_embedding = kb.create_embeddings(
        [question],
        task_type="RETRIEVAL_QUERY",
    )[0]
    note_rows = []
    embeddings = []

    for row in rows:
        vector = np.frombuffer(
            row["embedding"],
            dtype=np.float32,
        ).copy()

        if vector.shape[0] != row["embedding_dimensions"]:
            continue

        if vector.shape[0] != question_embedding.shape[0]:
            raise ValueError(
                "The verified notes use a different embedding "
                "model. Re-publish the affected notes."
            )

        note_rows.append(row)
        embeddings.append(vector)

    if not embeddings:
        return []

    matrix = np.vstack(embeddings)
    norms = np.linalg.norm(
        matrix,
        axis=1,
        keepdims=True,
    )
    norms = np.clip(norms, 1e-12, None)
    matrix = matrix / norms

    scores = matrix @ question_embedding
    result_count = min(top_k, len(scores))
    best_indexes = np.argsort(scores)[::-1][:result_count]
    results = []

    for index in best_indexes:
        row = note_rows[index]
        note_id = row["id"]

        results.append(
            {
                "kind": "verified_note",
                "note_id": note_id,
                "source": (
                    f"Verified Library Note #{note_id}"
                ),
                "citation": (
                    f"Verified Library Note #{note_id}"
                ),
                "title": row["title"],
                "text": row["content"],
                "source_reference": row[
                    "source_reference"
                ],
                "score": float(scores[index]),
            }
        )

    return results
