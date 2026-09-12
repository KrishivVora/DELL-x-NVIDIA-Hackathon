"""Retrieval adapter.

Primary backend is the ingestion lane's module (see CONTRACT.md section 3).
If it is unavailable, fall back to a local keyword scorer over extracted/ so the
end-to-end audit still runs. The backend used is always reported, so a demo
never silently claims semantic retrieval it did not perform.
"""

from __future__ import annotations

import importlib
import math
import os
import re
from collections import Counter

from .store import Project

STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "in", "on", "for", "to", "is", "are",
    "was", "were", "be", "by", "with", "that", "this", "it", "its", "as", "we",
    "our", "at", "from", "than", "then", "when", "which", "not", "but", "can",
}


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9.]+", text.lower()) if t not in STOPWORDS and len(t) > 1]


def retrieve(project: Project, query: str, k: int = 5) -> dict:
    override = os.environ.get("LABMATE_RAG_MODULE")
    candidates = [override] if override else ["labmate_rag", "claimtrace_rag"]
    if os.environ.get("LABMATE_FORCE_FALLBACK") == "1":
        reason = "LABMATE_FORCE_FALLBACK=1"
    else:
        reason = f"none of {candidates} importable"
        for module_name in candidates:
            try:
                mod = importlib.import_module(module_name)
            except ModuleNotFoundError:
                continue
            try:
                results = mod.retrieve(project.id, query, k)
                return {"backend": module_name, "query": query,
                        "results": [_normalize(x) for x in results][:k]}
            except Exception as exc:  # noqa: BLE001 - degrade instead of failing
                reason = f"{module_name} raised {type(exc).__name__}: {exc}"
                break

    return {
        "backend": "local-keyword-fallback",
        "fallback_reason": reason,
        "query": query,
        "results": _keyword_search(project, query, k),
    }


def _normalize(r: dict) -> dict:
    return {
        "path": r["path"],
        "page": r.get("page"),
        "section": r.get("section"),
        "text": r["text"],
        "score": float(r.get("score", 0.0)),
    }


def _keyword_search(project: Project, query: str, k: int) -> list[dict]:
    """Chunk extracted text by page, score by tf-idf-ish term overlap."""
    chunks: list[dict] = []
    for doc in project.documents():
        rel = doc.get("extracted_path")
        if not rel:
            continue
        path = project.resolve(rel)
        if not path.is_file():
            continue
        page, buf = None, []
        for line in path.read_text(errors="replace").splitlines():
            marker = re.fullmatch(r"<<<PAGE (\d+)>>>", line.strip())
            if marker:
                if buf:
                    chunks.append({"path": doc["path"], "page": page, "text": "\n".join(buf).strip()})
                page, buf = int(marker.group(1)), []
            else:
                buf.append(line)
        if buf:
            chunks.append({"path": doc["path"], "page": page, "text": "\n".join(buf).strip()})

    chunks = [c for c in chunks if c["text"]]
    if not chunks:
        return []

    doc_freq = Counter()
    for c in chunks:
        doc_freq.update(set(_tokens(c["text"])))
    n = len(chunks)
    q_terms = _tokens(query)

    scored = []
    for c in chunks:
        counts = Counter(_tokens(c["text"]))
        score = sum(
            counts[t] * math.log((n + 1) / (doc_freq[t] + 1)) for t in q_terms if counts[t]
        )
        if score > 0:
            scored.append({**c, "section": None, "score": round(score, 4)})
    scored.sort(key=lambda c: c["score"], reverse=True)
    for c in scored:
        c["text"] = c["text"][:1200]
    return scored[:k]
