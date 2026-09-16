import hashlib
import io
import re
import sqlite3
from pathlib import Path

import numpy as np
from pypdf import PdfReader

import ai_provider as ai


PROJECT_DIRECTORY = Path(__file__).resolve().parent
DATA_DIRECTORY = PROJECT_DIRECTORY / "data"
DATABASE_PATH = DATA_DIRECTORY / "libraguide_gemini.db"

EMBEDDING_MODEL = ai.EMBEDDING_MODEL

CHUNK_SIZE = 220
CHUNK_OVERLAP = 40


def get_connection():
    """Open a database connection with foreign keys enabled."""
    DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


def init_database():
    """Create the persistent knowledge-base tables."""
    with get_connection() as connection:
        connection.execute("PRAGMA journal_mode = WAL")

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL UNIQUE,
                file_hash TEXT NOT NULL UNIQUE,
                page_count INTEGER NOT NULL,
                chunk_count INTEGER NOT NULL,
                added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                page_number INTEGER NOT NULL,
                chunk_number INTEGER NOT NULL,
                content TEXT NOT NULL,
                embedding BLOB NOT NULL,
                embedding_dimensions INTEGER NOT NULL,
                FOREIGN KEY (document_id)
                    REFERENCES documents(id)
                    ON DELETE CASCADE
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chunks_document
            ON chunks(document_id)
            """
        )


def clean_text(text):
    """Remove excessive spaces and line breaks."""
    return re.sub(r"\s+", " ", text).strip()


def split_text(
    text,
    chunk_size=CHUNK_SIZE,
    overlap=CHUNK_OVERLAP,
):
    """Split text into overlapping word chunks."""
    words = text.split()

    if not words:
        return []

    chunks = []
    start = 0

    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk = " ".join(words[start:end]).strip()

        if len(chunk) >= 80:
            chunks.append(chunk)

        if end >= len(words):
            break

        start = end - overlap

    return chunks


def extract_pdf(file_bytes):
    """Extract page-aware text chunks from a PDF."""
    reader = PdfReader(io.BytesIO(file_bytes))
    extracted_chunks = []

    for page_number, page in enumerate(reader.pages, start=1):
        try:
            page_text = clean_text(page.extract_text() or "")
        except Exception:
            page_text = ""

        page_chunks = split_text(page_text)

        for chunk_number, content in enumerate(
            page_chunks,
            start=1,
        ):
            extracted_chunks.append(
                {
                    "page": page_number,
                    "chunk_number": chunk_number,
                    "text": content,
                }
            )

    return extracted_chunks, len(reader.pages)


def create_embeddings(
    texts,
    batch_size=16,
    task_type="RETRIEVAL_DOCUMENT",
):
    """Generate normalized embeddings through Gemini."""
    return ai.create_embeddings(
        texts=texts,
        task_type=task_type,
        batch_size=batch_size,
    )


def index_pdf(filename, file_bytes):
    """
    Add or update a PDF in the persistent knowledge base.

    An unchanged file is skipped. A new file using an existing
    filename replaces the previous version.
    """
    init_database()

    file_hash = hashlib.sha256(file_bytes).hexdigest()

    with get_connection() as connection:
        same_hash = connection.execute(
            """
            SELECT id, filename
            FROM documents
            WHERE file_hash = ?
            """,
            (file_hash,),
        ).fetchone()

        if same_hash:
            return {
                "status": "unchanged",
                "message": (
                    f"This document is already indexed as "
                    f"{same_hash['filename']}."
                ),
            }

    chunks, page_count = extract_pdf(file_bytes)

    if not chunks:
        raise ValueError(
            "No selectable text was extracted. "
            "The PDF may be scanned or image-only."
        )

    embeddings = create_embeddings(
        [chunk["text"] for chunk in chunks]
    )

    with get_connection() as connection:
        connection.execute(
            """
            DELETE FROM documents
            WHERE filename = ?
            """,
            (filename,),
        )

        cursor = connection.execute(
            """
            INSERT INTO documents (
                filename,
                file_hash,
                page_count,
                chunk_count
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                filename,
                file_hash,
                page_count,
                len(chunks),
            ),
        )

        document_id = cursor.lastrowid

        rows = []

        for chunk, embedding in zip(chunks, embeddings):
            rows.append(
                (
                    document_id,
                    chunk["page"],
                    chunk["chunk_number"],
                    chunk["text"],
                    embedding.astype(
                        np.float32
                    ).tobytes(),
                    int(embedding.shape[0]),
                )
            )

        connection.executemany(
            """
            INSERT INTO chunks (
                document_id,
                page_number,
                chunk_number,
                content,
                embedding,
                embedding_dimensions
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    return {
        "status": "indexed",
        "message": (
            f"Indexed {filename}: {page_count} page(s) "
            f"and {len(chunks)} passage(s)."
        ),
    }


def list_documents():
    """Return all documents in the shared knowledge base."""
    init_database()

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                filename,
                page_count,
                chunk_count,
                added_at
            FROM documents
            ORDER BY added_at DESC, filename ASC
            """
        ).fetchall()

    return [dict(row) for row in rows]


def delete_document(document_id):
    """Delete a document and all its chunks."""
    init_database()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            DELETE FROM documents
            WHERE id = ?
            """,
            (document_id,),
        )

    return cursor.rowcount > 0


def load_knowledge_base():
    """Load persistent chunks and vectors from SQLite."""
    init_database()

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                chunks.id,
                documents.filename,
                chunks.page_number,
                chunks.content,
                chunks.embedding,
                chunks.embedding_dimensions
            FROM chunks
            JOIN documents
                ON documents.id = chunks.document_id
            ORDER BY chunks.id
            """
        ).fetchall()

    if not rows:
        return [], np.empty(
            (0, 0),
            dtype=np.float32,
        )

    passages = []
    embeddings = []

    for row in rows:
        vector = np.frombuffer(
            row["embedding"],
            dtype=np.float32,
        ).copy()

        if vector.shape[0] != row["embedding_dimensions"]:
            continue

        passages.append(
            {
                "chunk_id": row["id"],
                "source": row["filename"],
                "page": row["page_number"],
                "text": row["content"],
            }
        )

        embeddings.append(vector)

    if not embeddings:
        return [], np.empty(
            (0, 0),
            dtype=np.float32,
        )

    matrix = np.vstack(embeddings)

    norms = np.linalg.norm(
        matrix,
        axis=1,
        keepdims=True,
    )

    norms = np.clip(norms, 1e-12, None)
    matrix = matrix / norms

    return passages, matrix


def search_knowledge_base(question, top_k=5):
    """Find the most relevant persistent knowledge passages."""
    passages, document_embeddings = load_knowledge_base()

    if not passages:
        return []

    question_embedding = create_embeddings(
        [question],
        task_type="RETRIEVAL_QUERY",
    )[0]

    if (
        document_embeddings.shape[1]
        != question_embedding.shape[0]
    ):
        raise ValueError(
            "The stored embeddings use a different model. "
            "Re-index the documents."
        )

    similarity_scores = (
        document_embeddings @ question_embedding
    )

    result_count = min(
        top_k,
        len(similarity_scores),
    )

    best_indexes = np.argsort(
        similarity_scores
    )[::-1][:result_count]

    results = []

    for index in best_indexes:
        item = dict(passages[index])
        item["score"] = float(
            similarity_scores[index]
        )
        results.append(item)

    return results
