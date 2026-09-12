#!/usr/bin/env python3
"""Turn a folder of files into a Labmate project you can talk to.

    python3 scripts/make-project.py my-notes demo-data/*.pdf

Creates projects/my-notes/ with originals/, extracted text, and manifest.json.
This is a stopgap so the agent is usable before the ingestion lane is ready;
once ingest.py exists it writes the same manifest and this script retires.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def pdf_pages_pymupdf(path: Path) -> list[str] | None:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None
    with fitz.open(path) as doc:
        return [page.get_text() for page in doc]


def pdf_pages_fallback(path: Path) -> list[str]:
    """Crude content-stream text extraction, so this works without PyMuPDF."""
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
    return pages


def extract(path: Path) -> tuple[list[str], str]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        pages = pdf_pages_pymupdf(path)
        if pages is not None:
            return pages, "ok"
        pages = pdf_pages_fallback(path)
        return pages, ("partial" if pages else "failed")
    if suffix in (".txt", ".md", ".yaml", ".yml", ".json", ".py"):
        return [path.read_text(errors="replace")], "ok"
    return [], "not_required"


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 1

    project_id, sources = argv[1], [Path(s) for s in argv[2:]]
    root = Path("projects") / project_id
    (root / "originals").mkdir(parents=True, exist_ok=True)
    (root / "extracted").mkdir(parents=True, exist_ok=True)

    documents = []
    for src in sources:
        if not src.is_file():
            print(f"  skip  {src} (not a file)")
            continue
        dest = root / "originals" / src.name
        shutil.copy2(src, dest)
        pages, status = extract(dest)

        extracted_path = None
        if pages:
            extracted_path = f"extracted/{src.stem}.txt"
            body = "\n".join(f"<<<PAGE {i}>>>\n{p}" for i, p in enumerate(pages, start=1))
            (root / extracted_path).write_text(body)

        documents.append(
            {
                "path": f"originals/{src.name}",
                "role": "evidence",
                "artifact_type": "document" if src.suffix.lower() == ".pdf" else "data",
                "mime_type": "application/pdf" if src.suffix.lower() == ".pdf" else "text/plain",
                "sha256": sha256(dest),
                "modified_at": datetime.fromtimestamp(dest.stat().st_mtime, tz=timezone.utc)
                .isoformat(timespec="seconds").replace("+00:00", "Z"),
                "extraction_status": status,
                "extracted_path": extracted_path,
                "pages": len(pages) or None,
            }
        )
        print(f"  added {src.name}  ({len(pages)} pages, extraction {status})")

    # the largest PDF is the most likely manuscript; override in manifest.json if wrong
    pdfs = [d for d in documents if d["path"].lower().endswith(".pdf")]
    if pdfs:
        max(pdfs, key=lambda d: d["pages"] or 0)["role"] = "manuscript"

    (root / "manifest.json").write_text(
        json.dumps(
            {
                "project_id": project_id,
                "generated_at": datetime.now(timezone.utc)
                .isoformat(timespec="seconds").replace("+00:00", "Z"),
                "documents": documents,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\nProject ready: {root}")
    print(f"Try:  python3 -m labmate --project {project_id} ask --query 'what is this about?'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
