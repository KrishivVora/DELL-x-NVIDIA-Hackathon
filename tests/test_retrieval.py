import pytest

from claimtrace import mongodb
from claimtrace.ingest import ingest_file
from claimtrace.retrieval import retrieve_candidates

PROJECT_ID = "test-project-retrieval"
PDF_PATH = "demo-data/structural.pdf"


@pytest.fixture(autouse=True)
def cleanup():
    yield
    db = mongodb.get_db()
    db.documents.delete_many({"project_id": PROJECT_ID})
    db.chunks.delete_many({"project_id": PROJECT_ID})


def test_retrieve_candidates_ranks_by_similarity():
    ingest_file(PROJECT_ID, PDF_PATH)

    results = retrieve_candidates(PROJECT_ID, "mathematical induction proof", top_k=3)

    assert len(results) == 3
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)
    for r in results:
        assert -1.0 <= r["score"] <= 1.0
        assert r["path"] == PDF_PATH
        assert r["text"]


def test_retrieve_candidates_empty_project_returns_empty():
    assert retrieve_candidates("no-such-project", "anything") == []
