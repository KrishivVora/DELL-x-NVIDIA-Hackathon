"""Text extraction and chunking.

Extracted text is written with `<<<PAGE n>>>` markers on their own line
(CONTRACT.md §2) so the agent and the keyword fallback can cite pages. Chunks
never cross a page boundary, so every chunk maps to exactly one (path, page).
"""

from __future__ import annotations

from pathlib import Path

CHUNK_SIZE = 500
OVERLAP = 50

TEXT_LIKE = {".txt", ".md", ".yaml", ".yml", ".json", ".py", ".toml", ".cfg", ".ini"}

# Separators tried in order: paragraph → line → sentence → word → character
_SEPARATORS = ["\n\n", "\n", ". ", "! ", "? ", " ", ""]


def extract_pages(path: Path) -> tuple[list[str], str]:
    """Return (pages, extraction_status). CSVs are deliberately not extracted:
    the agent reads them with pandas (CONTRACT.md §2)."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import pymupdf

        try:
            with pymupdf.open(path) as doc:
                pages = [page.get_text().strip() for page in doc]
        except Exception:  # noqa: BLE001 - corrupt, empty, or encrypted PDF
            return [], "failed"
        if not pages or not any(pages):
            return pages, "failed"
        return pages, ("partial" if any(not p for p in pages) else "ok")
    if suffix in TEXT_LIKE:
        return [path.read_text(errors="replace").strip()], "ok"
    return [], "not_required"


def render_extracted(pages: list[str]) -> str:
    return "\n".join(f"<<<PAGE {i}>>>\n{text}" for i, text in enumerate(pages, start=1)) + "\n"


def chunk_pages(pages: list[str], chunk_size: int = CHUNK_SIZE, overlap: int = OVERLAP) -> list[dict]:
    """[{page, text}] for every page with text; empty (scanned) pages are skipped."""
    chunks = []
    for page_num, page_text in enumerate(pages, start=1):
        for text in chunk_text(page_text, chunk_size, overlap):
            chunks.append({"page": page_num, "text": text})
    return chunks


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = OVERLAP) -> list[str]:
    pieces = _split_recursive(text.strip(), _SEPARATORS, chunk_size)
    return _merge_with_overlap(pieces, chunk_size, overlap)


def _split_recursive(text: str, separators: list[str], chunk_size: int) -> list[str]:
    """Split text into pieces that each fit within chunk_size, trying separators in order."""
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    sep = next((s for s in separators if s == "" or s in text), None)
    if sep is None:
        return [text]

    next_seps = separators[separators.index(sep) + 1:]
    parts = text.split(sep) if sep != "" else list(text)
    result = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) <= chunk_size:
            result.append(part)
        else:
            result.extend(_split_recursive(part, next_seps, chunk_size))
    return result


def _merge_with_overlap(pieces: list[str], chunk_size: int, overlap: int) -> list[str]:
    """Combine pieces into chunks up to chunk_size, carrying the last `overlap`
    characters of each finished chunk into the start of the next."""
    chunks = []
    buf = ""
    for piece in pieces:
        candidate = f"{buf} {piece}".strip() if buf else piece
        if len(candidate) <= chunk_size:
            buf = candidate
        else:
            if buf:
                chunks.append(buf)
            tail = buf[-overlap:] if buf and overlap else ""
            buf = f"{tail} {piece}".strip() if tail else piece
    if buf:
        chunks.append(buf)
    return chunks
