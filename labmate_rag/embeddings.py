"""Local embedding generation on the CPU (CONTRACT.md §3; spec "on-device vectors").

CPU on purpose: the GPU belongs to vLLM. all-MiniLM-L6-v2 embeds a few hundred
chunks in seconds on the Grace cores, and the model is ~90 MB.
"""

from __future__ import annotations

from . import config

_model = None


def model():
    global _model
    if _model is None:
        config.offline_models()
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(config.EMBEDDING_MODEL, device="cpu")
    return _model


def dimension() -> int:
    m = model()
    getter = getattr(m, "get_embedding_dimension", None) or m.get_sentence_embedding_dimension
    return int(getter())


def embed_texts(texts: list[str]) -> list[list[float]]:
    """One unit-length vector per text, so cosine similarity is a dot product."""
    if not texts:
        return []
    vectors = model().encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]
