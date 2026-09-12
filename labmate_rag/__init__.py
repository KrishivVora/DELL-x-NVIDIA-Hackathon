"""Ingestion / RAG lane (CONTRACT.md §1–3).

The agent layer imports exactly one thing from here:

    import labmate_rag
    labmate_rag.retrieve(project_id, query, k) -> list[dict]

Heavy dependencies (PyMuPDF, sentence-transformers, pymongo) and the MongoDB
connection live on the GB10 host. Inside the Hermes sandbox none of that is
available, so `retrieve` transparently calls the host API instead
(`labmate_rag serve`, reached at LABMATE_RAG_URL, default
http://host.openshell.internal:8700). Same function, same result shape,
wherever it is imported.
"""

from __future__ import annotations

import importlib.util
import os

DEFAULT_RAG_URL = "http://host.openshell.internal:8700"


def _local_backend_available() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in ("pymongo", "sentence_transformers", "pymupdf"))


def retrieve(project_id: str, query: str, k: int = 5) -> list[dict]:
    """Return up to k relevant passages, best first (CONTRACT.md §3)."""
    url = os.environ.get("LABMATE_RAG_URL")
    if url or not _local_backend_available():
        from .client import retrieve as remote_retrieve

        return remote_retrieve(project_id, query, k, base_url=url or DEFAULT_RAG_URL)
    from .retrieval import retrieve as local_retrieve

    return local_retrieve(project_id, query, k)


__all__ = ["retrieve", "DEFAULT_RAG_URL"]
