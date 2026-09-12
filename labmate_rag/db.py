"""MongoDB access: documents, chunks, and the two search indexes.

Runs against the box's MongoDB Atlas Local container, which supports
$vectorSearch, full-text $search, and multi-document transactions locally
(all verified on the GB10 on 2026-09-12). One database, records scoped by
project_id.
"""

from __future__ import annotations

import time

from pymongo import ASCENDING, MongoClient
from pymongo.database import Database

from . import config

VECTOR_INDEX = "chunks_vector"
TEXT_INDEX = "chunks_text"

_client: MongoClient | None = None
_indexes_ready: set[str] = set()


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(config.mongodb_uri(), serverSelectionTimeoutMS=5000)
    return _client


def reset_client() -> None:
    """Forget the cached client (tests switch databases)."""
    global _client
    if _client is not None:
        _client.close()
    _client = None
    _indexes_ready.clear()


def get_db() -> Database:
    return get_client()[config.db_name()]


def ping() -> dict:
    return get_client().admin.command("ping")


# -- indexes -----------------------------------------------------------------


def search_index_status(db: Database) -> dict[str, dict]:
    return {ix["name"]: ix for ix in db.chunks.aggregate([{"$listSearchIndexes": {}}])}


def ensure_indexes(db: Database, dimension: int, wait: bool = True, timeout: float = 120.0) -> None:
    """Create lookup and search indexes once per process; idempotent on the server."""
    key = f"{db.name}:{dimension}"
    if key in _indexes_ready:
        return
    db.documents.create_index([("project_id", ASCENDING), ("path", ASCENDING)], unique=True)
    db.chunks.create_index([("project_id", ASCENDING), ("document_id", ASCENDING)])

    existing = search_index_status(db)
    wanted = []
    if VECTOR_INDEX not in existing:
        wanted.append({
            "name": VECTOR_INDEX,
            "type": "vectorSearch",
            "definition": {"fields": [
                {"type": "vector", "path": "embedding", "numDimensions": dimension, "similarity": "cosine"},
                {"type": "filter", "path": "project_id"},
                {"type": "filter", "path": "document_id"},
            ]},
        })
    if TEXT_INDEX not in existing:
        wanted.append({
            "name": TEXT_INDEX,
            "type": "search",
            "definition": {"mappings": {"dynamic": False, "fields": {
                "text": {"type": "string"},
                "project_id": {"type": "token"},
                "document_id": {"type": "token"},
            }}},
        })
    if wanted:
        db.command({"createSearchIndexes": "chunks", "indexes": wanted})
    if wait:
        wait_for_search_indexes(db, timeout)
    _indexes_ready.add(key)


def wait_for_search_indexes(db: Database, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        status = search_index_status(db)
        if all(status.get(n, {}).get("queryable") for n in (VECTOR_INDEX, TEXT_INDEX)):
            return
        if time.monotonic() > deadline:
            raise TimeoutError(f"search indexes not queryable after {timeout}s: {status}")
        time.sleep(0.5)


def wait_for_indexed(db: Database, project_id: str, document_id: str, expected: int, timeout: float = 30.0) -> bool:
    """Search indexes update asynchronously; wait until this document's chunks are searchable."""
    deadline = time.monotonic() + timeout
    while True:
        rows = list(db.chunks.aggregate([
            {"$search": {"index": TEXT_INDEX, "compound": {"filter": [
                {"equals": {"path": "project_id", "value": project_id}},
                {"equals": {"path": "document_id", "value": document_id}},
            ]}}},
            {"$count": "n"},
        ]))
        n = rows[0]["n"] if rows else 0
        if n == expected:
            return True
        if time.monotonic() > deadline:
            return False
        time.sleep(0.5)


# -- records -----------------------------------------------------------------


def get_document(db: Database, project_id: str, path: str) -> dict | None:
    return db.documents.find_one({"project_id": project_id, "path": path})


def chunk_count(db: Database, project_id: str, document_id: str) -> int:
    return db.chunks.count_documents({"project_id": project_id, "document_id": document_id})


def upsert_document(db: Database, project_id: str, document: dict) -> None:
    db.documents.update_one(
        {"project_id": project_id, "path": document["path"]},
        {"$set": {**document, "project_id": project_id}},
        upsert=True,
    )


def replace_chunks(db: Database, project_id: str, document_id: str, chunks: list[dict]) -> None:
    """Swap a document's chunks atomically, so a re-ingest never leaves a mix of old and new."""
    client = get_client()
    with client.start_session() as session, session.start_transaction():
        coll = client[db.name].chunks
        coll.delete_many({"project_id": project_id, "document_id": document_id}, session=session)
        if chunks:
            coll.insert_many(
                [{**c, "project_id": project_id, "document_id": document_id} for c in chunks],
                session=session,
            )


def remove_document(db: Database, project_id: str, path: str, document_id: str) -> None:
    client = get_client()
    with client.start_session() as session, session.start_transaction():
        client[db.name].chunks.delete_many({"project_id": project_id, "document_id": document_id}, session=session)
        client[db.name].documents.delete_one({"project_id": project_id, "path": path}, session=session)


def project_documents(db: Database, project_id: str) -> list[dict]:
    return list(db.documents.find({"project_id": project_id}, {"_id": 0}))
