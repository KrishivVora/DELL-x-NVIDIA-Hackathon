"""Semantic + lexical evidence retrieval on the host (CONTRACT.md §3).

Both searches run natively in MongoDB Atlas Local: $vectorSearch over the
chunk embeddings and full-text $search over the chunk text, each filtered to
the project. The two rankings are fused in Python with reciprocal rank fusion
because $rankFusion is not available on the box's MongoDB 8.0.28. Lexical
search matters here: claims are full of exact tokens ("12%", "CIFAR-10",
"epochs: 50") that embeddings blur.

Retrieval nominates candidates. It never verifies anything (CONTRACT.md §4).
"""

from __future__ import annotations

from pymongo.errors import OperationFailure

from . import db, embeddings

RRF_K = 60
MAX_TEXT = 1200


class ProjectNotIndexed(LookupError):
    """Raised (not silently empty) so labmate/retrieval.py falls back to its
    keyword search and reports why, instead of answering from nothing."""


def retrieve(project_id: str, query: str, k: int = 5) -> list[dict]:
    """Return up to k passages as {path, page, section, text, score}, best first."""
    if not query or not query.strip():
        return []
    k = max(1, int(k))
    database = db.get_db()
    if database.documents.count_documents({"project_id": project_id}, limit=1) == 0:
        raise ProjectNotIndexed(
            f"project '{project_id}' has not been ingested; run: python3 -m labmate_rag ingest {project_id}"
        )
    pool = max(20, 4 * k)

    vector_hits = _vector_search(database, project_id, query, pool)
    text_hits = _text_search(database, project_id, query, pool)

    fused: dict = {}
    for hits in (vector_hits, text_hits):
        for rank, hit in enumerate(hits, start=1):
            entry = fused.setdefault(hit["_id"], {**hit, "score": 0.0})
            entry["score"] += 1.0 / (RRF_K + rank)

    ranked = sorted(fused.values(), key=lambda h: h["score"], reverse=True)[:k]
    return [{
        "path": h["path"],
        "page": h.get("page"),
        "section": h.get("section"),
        "text": h["text"][:MAX_TEXT],
        "score": round(h["score"], 6),
    } for h in ranked]


_PROJECTION = {"path": 1, "page": 1, "section": 1, "text": 1}


def _vector_search(database, project_id: str, query: str, limit: int) -> list[dict]:
    vector = embeddings.embed_texts([query])[0]
    return list(database.chunks.aggregate([
        {"$vectorSearch": {
            "index": db.VECTOR_INDEX, "path": "embedding", "queryVector": vector,
            "numCandidates": max(100, 10 * limit), "limit": limit,
            "filter": {"project_id": project_id},
        }},
        {"$project": {**_PROJECTION, "vector_score": {"$meta": "vectorSearchScore"}}},
    ]))


def _text_search(database, project_id: str, query: str, limit: int) -> list[dict]:
    try:
        return list(database.chunks.aggregate([
            {"$search": {"index": db.TEXT_INDEX, "compound": {
                "must": [{"text": {"query": query, "path": "text"}}],
                "filter": [{"equals": {"path": "project_id", "value": project_id}}],
            }}},
            {"$limit": limit},
            {"$project": {**_PROJECTION, "text_score": {"$meta": "searchScore"}}},
        ]))
    except OperationFailure:
        # Text index missing or not yet queryable: vector results alone are still valid.
        return []
