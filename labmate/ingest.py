"""Local ingestion: turn a file on disk into a manifest-registered document.

Used by the Slack intake path and by make-project.py. This handles the *local*
half — copy into originals/, extract page-marked text, upsert manifest.json.
The embeddings/vector half is the RAG lane's job; if LABMATE_INGEST_CMD is set,
this hands off to it after the file lands, so the two pipelines compose instead
of competing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import zlib
from datetime import datetime, timezone
from pathlib import Path

from .store import Project, now

TEXT_SUFFIXES = (".txt", ".md", ".yaml", ".yml", ".json", ".py", ".csv", ".tsv")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _pdf_pages(path: Path) -> tuple[list[str], str]:
    try:
        import fitz  # PyMuPDF
        with fitz.open(path) as doc:
            return [page.get_text() for page in doc], "ok"
    except ImportError:
        pass
    # fallback: crude content-stream extraction, no dependency
    raw = path.read_bytes()
    pages = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
        try:
            data = zlib.decompress(m.group(1))
        except Exception:
            continue
        parts = re.findall(rb"\((?:\\.|[^\\()])*\)", data)
        text = b" ".join(p[1:-1] for p in parts).decode("latin-1", "replace")
        text = re.sub(r"\\([()])", r"\1", text)
        text = re.sub(r"[ \t]+", " ", text).strip()
        if text:
            pages.append(text)
    return pages, ("partial" if pages else "failed")


def _extract(path: Path) -> tuple[list[str], str]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _pdf_pages(path)
    if suffix in (".csv", ".tsv"):
        return [], "not_required"  # tabular data is read directly, never embedded as text
    if suffix in TEXT_SUFFIXES:
        return [path.read_text(errors="replace")], "ok"
    return [], "not_required"


def _artifact_type(suffix: str) -> str:
    return {
        ".pdf": "document",
        ".csv": "experimental_data",
        ".tsv": "experimental_data",
    }.get(suffix, "data")


def ingest_path(project: Project, src: Path, role: str = "evidence") -> dict:
    """Bring `src` into the project and register it. Idempotent per filename.

    Returns the manifest document record. Raises on unreadable source.
    """
    src = Path(src)
    if not src.is_file():
        raise FileNotFoundError(f"no such file: {src}")

    project.originals.mkdir(parents=True, exist_ok=True)
    project.extracted.mkdir(parents=True, exist_ok=True)

    dest = project.originals / src.name
    # copy unless src already is the dest (re-ingest of an existing original)
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)

    pages, status = _extract(dest)
    extracted_path = None
    if pages:
        extracted_path = f"extracted/{src.stem}.txt"
        body = "\n".join(f"<<<PAGE {i}>>>\n{p}" for i, p in enumerate(pages, start=1))
        (project.root / extracted_path).write_text(body)

    record = {
        "path": f"originals/{src.name}",
        "role": role,
        "artifact_type": _artifact_type(src.suffix.lower()),
        "mime_type": "application/pdf" if src.suffix.lower() == ".pdf" else "text/plain",
        "sha256": _sha256(dest),
        "modified_at": datetime.fromtimestamp(dest.stat().st_mtime, tz=timezone.utc)
        .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "extraction_status": status,
        "extracted_path": extracted_path,
        "pages": len(pages) or None,
    }
    _upsert_manifest(project, record)
    project.log("ingested", path=record["path"], status=status)

    _handoff(project, dest)
    return record


def _upsert_manifest(project: Project, record: dict) -> None:
    if project.manifest_path.exists():
        manifest = json.loads(project.manifest_path.read_text())
    else:
        manifest = {"project_id": project.id, "documents": []}
    docs = manifest.setdefault("documents", [])
    for i, d in enumerate(docs):
        if d.get("path") == record["path"]:
            docs[i] = {**d, **record}
            break
    else:
        docs.append(record)
    manifest["project_id"] = project.id
    manifest["generated_at"] = now()
    project.manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


def _handoff(project: Project, dest: Path) -> None:
    """Hand the landed file to the RAG lane's embedding pipeline, if configured.

    LABMATE_INGEST_CMD is a shell template; {project} and {path} are filled with
    the project id and the absolute original path. Failure is logged, not fatal:
    the file is already usable through the local keyword fallback.
    """
    cmd = os.environ.get("LABMATE_INGEST_CMD")
    if not cmd:
        return
    command = cmd.format(project=shlex.quote(project.id), path=shlex.quote(str(dest)))
    try:
        proc = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=300)  # noqa: S602
        project.log("ingest_handoff", exit_code=proc.returncode)
    except Exception as exc:  # noqa: BLE001 - embedding is best-effort here
        project.log("ingest_handoff_failed", error=str(exc))
