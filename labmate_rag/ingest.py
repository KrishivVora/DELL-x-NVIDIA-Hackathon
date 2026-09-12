"""Project ingestion (CONTRACT.md §1–2).

`ingest_project` turns `projects/<id>/originals/` into:
  - `extracted/<name>.txt` with `<<<PAGE n>>>` markers, for PDFs and text-like files
  - `manifest.json` in the contract shape
  - MongoDB `documents` and `chunks` (with embeddings) for `retrieve()`

Unchanged files (same SHA-256) are skipped, so the watcher can call this
often. Originals are never written.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path

from . import config, db, embeddings, extract

_ARTIFACT_TYPES = {
    ".pdf": "document",
    ".csv": "experimental_data",
    ".tsv": "experimental_data",
    ".xlsx": "experimental_data",
    ".yaml": "config",
    ".yml": "config",
    ".json": "config",
    ".toml": "config",
    ".ipynb": "notebook",
    ".py": "code",
    ".txt": "notes",
    ".md": "notes",
}
_MIME_FALLBACK = {".yaml": "application/yaml", ".yml": "application/yaml", ".md": "text/markdown",
                  ".ipynb": "application/x-ipynb+json", ".toml": "application/toml"}


class IngestError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _now() -> str:
    return _iso(datetime.now(timezone.utc).timestamp())


def _extracted_rel(rel_path: str) -> str:
    # originals/sub/paper.pdf -> extracted/sub__paper.txt
    inner = Path(rel_path).relative_to("originals")
    return "extracted/" + "__".join(inner.with_suffix(".txt").parts)


def _mime(path: Path) -> str:
    return mimetypes.guess_type(str(path))[0] or _MIME_FALLBACK.get(path.suffix.lower(), "application/octet-stream")


def _list_originals(root: Path) -> list[Path]:
    originals = root / "originals"
    if not originals.is_dir():
        raise IngestError(f"{originals} does not exist; put source files under originals/")
    return sorted(p for p in originals.rglob("*") if p.is_file() and not p.name.startswith("."))


def _existing_roles(root: Path) -> dict[str, str]:
    manifest = root / "manifest.json"
    if not manifest.is_file():
        return {}
    try:
        docs = json.loads(manifest.read_text()).get("documents", [])
    except json.JSONDecodeError:
        return {}
    return {d["path"]: d.get("role", "evidence") for d in docs if "path" in d}


def ingest_project(project_id: str, *, root: Path | None = None, manuscript: str | None = None,
                   force: bool = False, wait_indexed: bool = True, index: bool = True) -> dict:
    """Ingest every file under originals/ and write manifest.json. Returns a summary.

    index=False writes only extracted/ and manifest.json (no MongoDB, no model),
    which is all the agent layer's keyword fallback needs. It replaces the old
    scripts/make-project.py stopgap.
    """
    root = Path(root) if root else config.projects_root() / project_id
    if not root.is_dir():
        raise IngestError(f"project '{project_id}' not found at {root}")
    (root / "extracted").mkdir(exist_ok=True)

    database = None
    previous: dict[str, dict] = {}
    if index:
        database = db.get_db()
        db.ensure_indexes(database, embeddings.dimension())
        previous = {d["path"]: d for d in db.project_documents(database, project_id)}
    prior_roles = _existing_roles(root)

    documents: list[dict] = []
    summary = {"project_id": project_id, "ingested": [], "skipped": [], "removed": [], "warnings": {}}

    for file in _list_originals(root):
        rel = file.relative_to(root).as_posix()          # originals/<...>
        document_id = rel
        sha = _sha256(file)
        suffix = file.suffix.lower()
        warnings: list[str] = []
        prev = previous.pop(rel, None)

        record = {
            "path": rel,
            "role": prior_roles.get(rel, "evidence"),
            "artifact_type": _ARTIFACT_TYPES.get(suffix, "other"),
            "mime_type": _mime(file),
            "sha256": sha,
            "modified_at": _iso(file.stat().st_mtime),
            "extraction_status": "not_required",
            "extracted_path": None,
            "pages": None,
        }

        extracted_rel = _extracted_rel(rel)
        unchanged = (prev is not None and prev.get("sha256") == sha and not force
                     and (prev.get("extraction_status") == "not_required" or (root / extracted_rel).is_file()))
        if unchanged:
            for key in ("extraction_status", "extracted_path", "pages", "warnings"):
                if key in prev:
                    record[key] = prev[key]
            record.pop("warnings", None)
            documents.append(record)
            summary["skipped"].append(rel)
            continue

        pages, status = extract.extract_pages(file)
        record["extraction_status"] = status
        chunks: list[dict] = []
        if status != "not_required":
            record["pages"] = len(pages) or None
            if status in ("ok", "partial"):
                (root / extracted_rel).parent.mkdir(parents=True, exist_ok=True)
                (root / extracted_rel).write_text(extract.render_extracted(pages))
                record["extracted_path"] = extracted_rel
                empty = [i for i, p in enumerate(pages, start=1) if not p]
                if empty:
                    warnings.append(f"no extractable text on pages {empty} (scanned? OCR is out of scope)")
                chunks = extract.chunk_pages(pages)
            else:
                warnings.append("no extractable text in the file")

        if index:
            if chunks:
                vectors = embeddings.embed_texts([c["text"] for c in chunks])
                for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
                    chunk.update({"path": rel, "section": None, "embedding": vector, "sha256": sha, "chunk_index": i})
            db.replace_chunks(database, project_id, document_id, chunks)
            db.upsert_document(database, project_id, {**record, "document_id": document_id,
                                                      "warnings": warnings, "ingested_at": _now()})
            if chunks and wait_indexed and not db.wait_for_indexed(database, project_id, document_id, len(chunks)):
                warnings.append("search index still catching up; retrieval may lag for a few seconds")

        documents.append(record)
        summary["ingested"].append({"path": rel, "chunks": len(chunks), "status": status})
        if warnings:
            summary["warnings"][rel] = warnings

    # Files that disappeared from originals/ leave the index too.
    for rel, prev in previous.items():
        db.remove_document(database, project_id, rel, prev.get("document_id", rel))
        summary["removed"].append(rel)
    summary["indexed"] = index

    _assign_manuscript(documents, manuscript)
    manifest = {"project_id": project_id, "generated_at": _now(), "documents": documents}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    summary["manifest"] = str(root / "manifest.json")
    summary["manuscript"] = next((d["path"] for d in documents if d["role"] == "manuscript"), None)
    return summary


def _assign_manuscript(documents: list[dict], manuscript: str | None) -> None:
    """Exactly one document is the manuscript: an explicit choice, else a prior
    role from manifest.json, else the PDF with the most pages."""
    if manuscript:
        wanted = manuscript if manuscript.startswith("originals/") else f"originals/{manuscript}"
        if not any(d["path"] == wanted for d in documents):
            raise IngestError(f"--manuscript {manuscript!r} is not under originals/")
        for d in documents:
            d["role"] = "manuscript" if d["path"] == wanted else "evidence"
    manuscripts = [d for d in documents if d["role"] == "manuscript"]
    if len(manuscripts) > 1:
        for d in manuscripts[1:]:
            d["role"] = "evidence"
    elif not manuscripts:
        pdfs = [d for d in documents if d["path"].lower().endswith(".pdf") and d.get("pages")]
        if pdfs:
            max(pdfs, key=lambda d: d["pages"])["role"] = "manuscript"
    for d in documents:
        if d["role"] == "manuscript" and d["artifact_type"] == "document":
            d["artifact_type"] = "manuscript"
