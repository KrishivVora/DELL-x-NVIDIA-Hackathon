"""Semantic evidence retrieval (spec §9).

Generates a local embedding for the audit query or extracted claim, then
retrieves the most relevant chunks. Local MongoDB Community (no Atlas) does
not support native $vectorSearch, so per the spec's own fallback (§9, §19)
this computes cosine similarity in Python over each project's stored
embeddings. Retrieval only narrows candidates; verifier.py performs the
actual checks.
"""

from __future__ import annotations

import numpy as np

from claimtrace import embeddings, mongodb


def retrieve_candidates(project_id: str, query: str, top_k: int = 5) -> list[dict]:
    """Return the top_k chunks most semantically similar to query, ranked
    by cosine similarity, each with a 'score' field."""
    db = mongodb.get_db()
    chunks = list(db.chunks.find({"project_id": project_id}))
    if not chunks:
        return []

    query_vector = np.array(embeddings.embed_texts([query])[0])
    chunk_vectors = np.array([c["embedding"] for c in chunks])

    query_norm = query_vector / np.linalg.norm(query_vector)
    chunk_norms = chunk_vectors / np.linalg.norm(chunk_vectors, axis=1, keepdims=True)
    scores = np.sum(chunk_norms * query_norm, axis=1)

    ranked = np.argsort(scores)[::-1][:top_k]
    return [{**chunks[i], "score": float(scores[i])} for i in ranked]
