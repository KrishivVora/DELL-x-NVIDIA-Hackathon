"""Ingestion / RAG lane tests. They run against the box's MongoDB Atlas Local
in a throwaway database (claimtrace_test) and a temporary projects root, so
they never touch demo data. Skipped where MongoDB credentials are absent.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

os.environ["LABMATE_DB_NAME"] = "claimtrace_test"

from labmate_rag import client, config, db  # noqa: E402
from labmate_rag.extract import chunk_text, extract_pages, render_extracted  # noqa: E402
from labmate_rag.ingest import ingest_project  # noqa: E402
from labmate_rag.retrieval import retrieve  # noqa: E402

try:
    config.mongodb_uri()
    HAVE_MONGO = True
except config.ConfigError:
    HAVE_MONGO = False

needs_mongo = pytest.mark.skipif(not HAVE_MONGO, reason="MongoDB credentials not configured on this machine")

PAGES = [
    "Results. Our method improves accuracy by 12% over the baseline on the test split.",
    "Methods. We evaluate on five datasets: CIFAR-10, CIFAR-100, SVHN, STL-10 and TinyImageNet.",
    "Discussion. The model is qualitatively more robust to label noise than prior work.",
]


def make_pdf(path: Path, pages: list[str]) -> None:
    import pymupdf

    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=11)
    doc.save(str(path))
    doc.close()


@pytest.fixture(scope="module")
def mongo():
    if not HAVE_MONGO:
        pytest.skip("no MongoDB")
    db.reset_client()
    yield db.get_db()
    db.get_client().drop_database(config.db_name())
    db.reset_client()


@pytest.fixture
def project(tmp_path, monkeypatch, mongo):
    monkeypatch.setenv("LABMATE_PROJECTS_ROOT", str(tmp_path))
    root = tmp_path / "proj-1"
    (root / "originals").mkdir(parents=True)
    make_pdf(root / "originals" / "manuscript.pdf", PAGES)
    (root / "originals" / "results.csv").write_text("method,split,accuracy\nbaseline,test,0.80\nours,test,0.886\n")
    (root / "originals" / "config.yaml").write_text("datasets: [cifar10, cifar100, svhn, stl10, tinyimagenet]\nepochs: 50\n")
    return root


# -- pure functions ------------------------------------------------------------


def test_chunk_text_respects_size_and_overlaps():
    text = " ".join(f"word{i}" for i in range(400))
    chunks = chunk_text(text, chunk_size=120, overlap=20)
    assert len(chunks) > 3
    assert all(len(c) <= 120 for c in chunks)
    assert chunks[1].startswith(chunks[0][-20:].split(" ", 1)[-1][:5]) or len(chunks[0]) < 20


def test_extract_pdf_pages_and_markers(tmp_path):
    pdf = tmp_path / "a.pdf"
    make_pdf(pdf, PAGES)
    pages, status = extract_pages(pdf)
    assert status == "ok" and len(pages) == 3
    assert "12%" in pages[0]
    rendered = render_extracted(pages)
    assert rendered.startswith("<<<PAGE 1>>>\n") and "<<<PAGE 3>>>" in rendered


def test_csv_is_not_extracted(tmp_path):
    csv = tmp_path / "r.csv"
    csv.write_text("a,b\n1,2\n")
    assert extract_pages(csv) == ([], "not_required")


# -- ingestion -----------------------------------------------------------------


def test_ingest_no_index_needs_no_mongodb(tmp_path, monkeypatch):
    """Replaces scripts/make-project.py: manifest + extracted/ without MongoDB or the model."""
    monkeypatch.setenv("LABMATE_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("MONGODB_URI", "mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=1")  # must not be used
    root = tmp_path / "offline"
    (root / "originals").mkdir(parents=True)
    make_pdf(root / "originals" / "paper.pdf", PAGES)
    (root / "originals" / "results.csv").write_text("a,b\n1,2\n")

    summary = ingest_project("offline", index=False)
    assert summary["indexed"] is False and summary["manuscript"] == "originals/paper.pdf"
    manifest = json.loads((root / "manifest.json").read_text())
    pdf = next(d for d in manifest["documents"] if d["path"] == "originals/paper.pdf")
    assert pdf["extraction_status"] == "ok" and (root / pdf["extracted_path"]).read_text().startswith("<<<PAGE 1>>>")


@needs_mongo
def test_ingest_writes_contract_manifest_and_extracted(project):
    summary = ingest_project("proj-1")
    manifest = json.loads((project / "manifest.json").read_text())
    assert manifest["project_id"] == "proj-1"
    docs = {d["path"]: d for d in manifest["documents"]}
    assert set(docs) == {"originals/manuscript.pdf", "originals/results.csv", "originals/config.yaml"}

    ms = docs["originals/manuscript.pdf"]
    assert ms["role"] == "manuscript" and ms["artifact_type"] == "manuscript"
    assert ms["extraction_status"] == "ok" and ms["pages"] == 3
    assert ms["extracted_path"] == "extracted/manuscript.txt"
    assert (project / ms["extracted_path"]).read_text().startswith("<<<PAGE 1>>>")

    csv = docs["originals/results.csv"]
    assert csv["role"] == "evidence" and csv["artifact_type"] == "experimental_data"
    assert csv["extraction_status"] == "not_required" and csv["extracted_path"] is None

    assert summary["manuscript"] == "originals/manuscript.pdf"
    assert len(summary["ingested"]) == 3 and not summary["skipped"]
    for d in manifest["documents"]:
        assert d["modified_at"].endswith("Z") and len(d["sha256"]) == 64


@needs_mongo
def test_reingest_skips_unchanged_and_replaces_changed(project, mongo):
    ingest_project("proj-1")
    before = db.chunk_count(mongo, "proj-1", "originals/manuscript.pdf")
    assert before > 0

    second = ingest_project("proj-1")
    assert len(second["skipped"]) == 3 and not second["ingested"]
    assert db.chunk_count(mongo, "proj-1", "originals/manuscript.pdf") == before

    make_pdf(project / "originals" / "manuscript.pdf", PAGES + ["Appendix. Extra material about ablations."])
    third = ingest_project("proj-1")
    assert [d["path"] for d in third["ingested"]] == ["originals/manuscript.pdf"]
    assert db.chunk_count(mongo, "proj-1", "originals/manuscript.pdf") >= before
    manifest = json.loads((project / "manifest.json").read_text())
    assert next(d for d in manifest["documents"] if d["path"].endswith(".pdf"))["pages"] == 4


@needs_mongo
def test_removed_file_leaves_index_and_manifest(project, mongo):
    ingest_project("proj-1")
    (project / "originals" / "config.yaml").unlink()
    summary = ingest_project("proj-1")
    assert summary["removed"] == ["originals/config.yaml"]
    assert db.get_document(mongo, "proj-1", "originals/config.yaml") is None
    manifest = json.loads((project / "manifest.json").read_text())
    assert all(d["path"] != "originals/config.yaml" for d in manifest["documents"])


@needs_mongo
def test_explicit_manuscript_choice(project):
    summary = ingest_project("proj-1", manuscript="config.yaml")
    assert summary["manuscript"] == "originals/config.yaml"


# -- retrieval -----------------------------------------------------------------


@needs_mongo
def test_retrieve_returns_contract_shape_and_right_page(project):
    ingest_project("proj-1")
    results = retrieve("proj-1", "how much did accuracy improve over the baseline", k=3)
    assert results and len(results) <= 3
    for r in results:
        assert set(r) == {"path", "page", "section", "text", "score"}
        assert r["path"] == "originals/manuscript.pdf" or r["path"] == "originals/config.yaml"
        assert r["text"] and isinstance(r["score"], float)
    assert results[0]["path"] == "originals/manuscript.pdf" and results[0]["page"] == 1
    assert [r["score"] for r in results] == sorted((r["score"] for r in results), reverse=True)


@needs_mongo
def test_retrieve_exact_token_query_uses_text_index(project):
    ingest_project("proj-1")
    results = retrieve("proj-1", "CIFAR-10 SVHN datasets", k=2)
    assert results[0]["page"] == 2


@needs_mongo
def test_retrieve_is_scoped_to_project(project):
    from labmate_rag.retrieval import ProjectNotIndexed

    ingest_project("proj-1")
    with pytest.raises(ProjectNotIndexed):  # raises so the agent layer falls back to keyword search
        retrieve("no-such-project", "accuracy", k=3)
    assert retrieve("proj-1", "   ", k=3) == []


# -- intake (a Slack upload landing on the host) --------------------------------


@needs_mongo
def test_accept_upload_lands_and_indexes_a_file(project, mongo):
    from labmate_rag.ingest import IngestError, accept_upload

    ingest_project("proj-1")
    summary = accept_upload("proj-1", "ablation.csv", b"dataset,score\nsvhn,0.91\n")
    assert summary["accepted"] == "originals/ablation.csv"
    assert (project / "originals" / "ablation.csv").is_file()
    assert not list((project / "originals").glob(".*.part"))
    manifest = json.loads((project / "manifest.json").read_text())
    assert any(d["path"] == "originals/ablation.csv" for d in manifest["documents"])

    notes = accept_upload("proj-1", "notes.md", b"# Noise sweep\nWe ran a noise sweep at levels 0.1 and 0.2.\n")
    assert notes["accepted"] == "originals/notes.md"
    assert retrieve("proj-1", "noise sweep levels", k=3)[0]["path"] == "originals/notes.md"

    with pytest.raises(IngestError, match="not allowed"):
        accept_upload("proj-1", "evil.sh", b"#!/bin/sh\nrm -rf /\n")
    with pytest.raises(IngestError, match="unsafe filename"):
        accept_upload("proj-1", "../escape.csv", b"a,b\n")


@needs_mongo
def test_accept_upload_creates_a_new_project(tmp_path, monkeypatch, mongo):
    from labmate_rag.ingest import accept_upload

    monkeypatch.setenv("LABMATE_PROJECTS_ROOT", str(tmp_path))
    summary = accept_upload("from-slack", "readme.md", b"# Shared in Slack\nA new project starts here.\n")
    assert summary["accepted"] == "originals/readme.md"
    assert (tmp_path / "from-slack" / "manifest.json").is_file()


# -- host API + sandbox client --------------------------------------------------


@needs_mongo
def test_api_and_client_round_trip(project, monkeypatch):
    from labmate_rag.api import Handler

    ingest_project("proj-1")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert client.health(base_url=base)["status"] == "ok"
        results = client.retrieve("proj-1", "robust to noise", k=2, base_url=base)
        assert results and results[0]["page"] == 3

        # The package-level function goes remote when LABMATE_RAG_URL is set.
        import labmate_rag
        monkeypatch.setenv("LABMATE_RAG_URL", base)
        assert labmate_rag.retrieve("proj-1", "robust to noise", k=1)[0]["page"] == 3

        summary = client.ingest("proj-1", base_url=base)
        assert len(summary["skipped"]) == 3

        # the sandbox path: bytes in, indexed document out
        up = client.intake("proj-1", "slack-upload.md", b"# From Slack\nThe ablation used five seeds.\n",
                           base_url=base)
        assert up["accepted"] == "originals/slack-upload.md"
        assert retrieve("proj-1", "how many seeds did the ablation use", k=3)[0]["path"] == "originals/slack-upload.md"
        with pytest.raises(client.RagApiError, match="not allowed"):
            client.intake("proj-1", "evil.sh", b"x", base_url=base)

        with pytest.raises(client.RagApiError):
            client.retrieve("proj-1", "x", base_url="http://127.0.0.1:1")
        with pytest.raises(client.RagApiError, match="not been ingested"):
            client.retrieve("never-ingested", "x", base_url=base)
    finally:
        httpd.shutdown()
        httpd.server_close()
