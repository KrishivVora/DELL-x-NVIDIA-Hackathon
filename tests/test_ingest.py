import pytest

from claimtrace import mongodb
from claimtrace.ingest import ingest_file

PROJECT_ID = "test-project"
PDF_PATH = "demo-data/structural.pdf"


@pytest.fixture(autouse=True)
def cleanup():
    yield
    db = mongodb.get_db()
    db.documents.delete_many({"project_id": PROJECT_ID})
    db.chunks.delete_many({"project_id": PROJECT_ID})


def test_ingest_pdf_stores_document_and_chunks():
    result = ingest_file(PROJECT_ID, PDF_PATH)

    assert result["artifact_type"] == "manuscript"
    assert result["chunk_count"] > 0

    db = mongodb.get_db()
    document = db.documents.find_one({"project_id": PROJECT_ID, "path": PDF_PATH})
    assert document is not None
    assert document["extraction_status"] in {"ok", "partial"}

    chunks = list(db.chunks.find({"project_id": PROJECT_ID, "document_id": result["document_id"]}))
    assert len(chunks) == result["chunk_count"]
    for chunk in chunks:
        assert chunk["path"] == PDF_PATH
        assert isinstance(chunk["page"], int)
        assert chunk["text"]
        assert len(chunk["embedding"]) > 0


def test_reingest_replaces_chunks_instead_of_duplicating():
    first = ingest_file(PROJECT_ID, PDF_PATH)
    second = ingest_file(PROJECT_ID, PDF_PATH)

    db = mongodb.get_db()
    count = db.chunks.count_documents({"project_id": PROJECT_ID, "document_id": second["document_id"]})
    assert count == first["chunk_count"] == second["chunk_count"]
