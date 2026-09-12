"""Local watched-folder ingestion (spec §7, §11).

Only the RAG-relevant part of the ingestion flow lives here: hashing,
artifact-type detection, PDF extraction/chunking/embedding, and storing the
result in MongoDB. Watching the folder itself (watcher.py), triggering an
audit, and OpenShell sandbox visibility are separate, non-RAG concerns.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
from datetime import datetime, timezone

from claimtrace import embeddings, mongodb
from claimtrace.extract_pdf import chunk_pdf

_ARTIFACT_TYPES = {
    ".pdf": "manuscript",
    ".csv": "experimental_data",
}


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def ingest_file(project_id: str, path: str) -> dict:
    """Ingest one file into a project: hash, type-detect, extract, and upsert.

    PDFs are extracted, chunked, embedded, and stored as searchable chunks.
    CSVs are recorded but not chunked/embedded — CSV verification is
    deterministic (pandas) rather than retrieval-based, so it's out of scope
    for this RAG pipeline.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext not in _ARTIFACT_TYPES:
        raise ValueError(f"Unsupported file type: {ext}")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    document_id = os.path.splitext(os.path.basename(path))[0]
    warnings: list[str] = []
    chunks: list[dict] = []

    if ext == ".pdf":
        result = chunk_pdf(path)
        chunks = result["chunks"]
        if result["scanned_pages"]:
            warnings.append(f"no extractable text on pages: {result['scanned_pages']}")
        if chunks:
            vectors = embeddings.embed_texts([c["text"] for c in chunks])
            for chunk, vector in zip(chunks, vectors):
                chunk["embedding"] = vector
        extraction_status = "partial" if warnings else "ok"
    else:
        extraction_status = "not_required"

    document = {
        "path": path,
        "sha256": _sha256(path),
        "artifact_type": _ARTIFACT_TYPES[ext],
        "mime_type": mimetypes.guess_type(path)[0],
        "modified_at": datetime.now(timezone.utc).isoformat(),
        "extraction_status": extraction_status,
        "warnings": warnings,
    }
    mongodb.upsert_document(project_id, document)
    if chunks:
        mongodb.upsert_chunks(project_id, document_id, chunks)

    return {
        "document_id": document_id,
        "artifact_type": document["artifact_type"],
        "chunk_count": len(chunks),
        "warnings": warnings,
    }
