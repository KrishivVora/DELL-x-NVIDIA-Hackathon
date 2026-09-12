from __future__ import annotations

import fitz  # PyMuPDF

CHUNK_SIZE = 500
OVERLAP = 50

# Separators tried in order: paragraph → line → sentence → word → character
_SEPARATORS = ["\n\n", "\n", ". ", "! ", "? ", " ", ""]


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = OVERLAP) -> list[str]:
    pieces = _split_recursive(text.strip(), _SEPARATORS, chunk_size)
    return _merge_with_overlap(pieces, chunk_size, overlap)


def extract_pdf_pages(path: str) -> tuple[list[str], list[int]]:
    """Return each page's extracted text, and the page numbers (1-indexed)
    that produced no text — likely scanned pages needing an OCR fallback."""
    pages = []
    scanned_pages = []
    with fitz.open(path) as doc:
        for page_num, page in enumerate(doc, start=1):
            text = page.get_text().strip()
            pages.append(text)
            if not text:
                scanned_pages.append(page_num)
    return pages, scanned_pages


def chunk_pdf(path: str, chunk_size: int = CHUNK_SIZE, overlap: int = OVERLAP) -> dict:
    """Extract and chunk a native-text PDF.

    Chunking runs per-page so overlap never blurs across a page boundary and
    every chunk can be traced back to a single source page, matching the
    ClaimTrace chunk record shape (path, page, text).

    Returns {"chunks": [...], "scanned_pages": [...]} — scanned_pages lists
    1-indexed pages with no extractable text, flagged for the OCR fallback.
    """
    pages, scanned_pages = extract_pdf_pages(path)
    chunks = []
    for page_num, page_text in enumerate(pages, start=1):
        if not page_text:
            continue
        for chunk in chunk_text(page_text, chunk_size, overlap):
            chunks.append({"text": chunk, "path": path, "page": page_num})
    return {"chunks": chunks, "scanned_pages": scanned_pages}


def _split_recursive(text: str, separators: list[str], chunk_size: int) -> list[str]:
    """Split text into pieces that each fit within chunk_size, trying separators in order."""
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    # Find the first separator that appears in the text
    sep = next((s for s in separators if s == "" or s in text), None)
    if sep is None:
        return [text]

    next_seps = separators[separators.index(sep) + 1:]

    parts = text.split(sep) if sep != "" else [text[i] for i in range(len(text))]
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


if __name__ == "__main__":
    import os

    DOCS_DIR = "demo-data"
    all_chunks = []
    for filename in sorted(os.listdir(DOCS_DIR)):
        if not filename.endswith(".pdf"):
            continue
        path = os.path.join(DOCS_DIR, filename)
        result = chunk_pdf(path)

        print(f"{filename} → {len(result['chunks'])} chunks", end="")
        if result["scanned_pages"]:
            print(f"  (scanned/no-text pages: {result['scanned_pages']})")
        else:
            print()

        all_chunks.extend(result["chunks"])

    print(f"\nTotal chunks: {len(all_chunks)}")

    # print 5 random chunks
    import random
    print("\nSample chunks:")
    for chunk in random.sample(all_chunks, min(5, len(all_chunks))):
        print(f"[path: {chunk['path']}]  [page: {chunk['page']}]")
        print(chunk["text"])
        print("---")
