"""Local embedding generation (spec §8: "On-device vector generation").

Storage of the resulting vectors is mongodb.py's responsibility, not this
module's.
"""

from __future__ import annotations

from sentence_transformers import SentenceTransformer

EMBEDDING_MODEL = "all-MiniLM-L6-v2"

_model: SentenceTransformer | None = None


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Return one embedding vector per input text, using the local model."""
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model.encode(texts).tolist()
