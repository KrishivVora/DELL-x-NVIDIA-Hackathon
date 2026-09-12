"""Local MongoDB access (spec §7, §10).

Stores document records and chunks (with embeddings) for each project in a
single local database, scoped by a `project_id` field on every record —
matching the example records in project plan §11. Claim records and audit
history are out of scope here (non-RAG; see verifier.py/manifest.py).
"""

from __future__ import annotations

import os

from pymongo import ASCENDING, MongoClient
from pymongo.database import Database

DB_NAME = "claimtrace"

_client: MongoClient | None = None


def get_client(uri: str | None = None) -> MongoClient:
    """Return a shared MongoClient, defaulting to a local MongoDB instance."""
    global _client
    if _client is None:
        uri = uri or os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
        _client = MongoClient(uri)
    return _client


def get_db() -> Database:
    """Return the claimtrace database, ensuring lookup indexes exist."""
    db = get_client()[DB_NAME]
    db.documents.create_index([("project_id", ASCENDING), ("path", ASCENDING)], unique=True)
    db.chunks.create_index([("project_id", ASCENDING), ("document_id", ASCENDING)])
    return db


def upsert_document(project_id: str, document: dict) -> None:
    """Insert or update a project's document record, keyed by (project_id, path)."""
    db = get_db()
    db.documents.update_one(
        {"project_id": project_id, "path": document["path"]},
        {"$set": {**document, "project_id": project_id}},
        upsert=True,
    )


def upsert_chunks(project_id: str, document_id: str, chunks: list[dict]) -> None:
    """Replace all chunks for (project_id, document_id) with the given list.

    Deleting the prior chunks before inserting keeps re-ingestion (a changed
    file) clean, with no stale chunks left behind from the old version.
    """
    db = get_db()
    db.chunks.delete_many({"project_id": project_id, "document_id": document_id})
    if chunks:
        db.chunks.insert_many(
            [{**chunk, "project_id": project_id, "document_id": document_id} for chunk in chunks]
        )
