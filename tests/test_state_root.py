"""LABMATE_STATE_ROOT: the agent's writes leave a read-only project untouched."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixture-project"


def run(argv: list[str]) -> dict:
    from labmate.cli import main
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["--project", "fixture-project", *argv])
    assert rc == 0, buf.getvalue()
    return json.loads(buf.getvalue())


@pytest.fixture
def readonly_project(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    # copy only the tracked inputs; ignore state a previous smoke run may have left behind
    shutil.copytree(FIXTURE, projects / "fixture-project",
                    ignore=shutil.ignore_patterns("audit-state.json", "reports", "meetings.json",
                                                  "__pycache__"))
    # A researcher-supplied meetings.json shipped inside the (read-only) project.
    # It is written here rather than tracked in the fixture: smoke.sh treats
    # tests/fixture-project/meetings.json as generated output and deletes it.
    (projects / "fixture-project" / "meetings.json").write_text(json.dumps(
        {"meetings": [{"id": "m-001", "title": "Weekly sync", "when": "2026-09-15T10:00",
                       "attendees": ["Dr. Rao"], "topics": ["accuracy improvements"],
                       "created_at": "2026-09-12T19:05:31Z"}]}, indent=2) + "\n")
    for p in (projects / "fixture-project").rglob("*"):
        p.chmod(p.stat().st_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
    for d in [projects / "fixture-project", *[p for p in (projects / "fixture-project").rglob("*") if p.is_dir()]]:
        d.chmod(stat.S_IRUSR | stat.S_IXUSR)
    monkeypatch.setenv("LABMATE_PROJECTS_ROOT", str(projects))
    monkeypatch.setenv("LABMATE_STATE_ROOT", str(tmp_path / "state"))
    yield projects / "fixture-project"
    for d in [projects / "fixture-project", *[p for p in (projects / "fixture-project").rglob("*") if p.is_dir()]]:
        d.chmod(stat.S_IRWXU)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_agent_writes_go_to_state_root(readonly_project, tmp_path):
    state_dir = tmp_path / "state" / "fixture-project"
    before = sorted(str(p.relative_to(readonly_project)) for p in readonly_project.rglob("*"))
    status = run(["project-status"])
    assert status["state_dir"] == str(state_dir)

    run(["claim-set", "--claim-id", "claim-009", "--claim", "x", "--status", "unverifiable"])
    run(["meeting-set", "--id", "m1", "--title", "sync", "--when", "2026-09-13T10:00:00Z"])
    report = run(["report"])
    assert report["latest"].startswith(str(state_dir))

    assert (state_dir / "audit-state.json").is_file()
    assert (state_dir / "meetings.json").is_file()
    assert (state_dir / "reports" / "latest.md").is_file()
    # nothing new inside the read-only project
    after = sorted(str(p.relative_to(readonly_project)) for p in readonly_project.rglob("*"))
    assert after == before
    # the researcher-supplied meetings.json still seeds the register
    ids = {m["id"] for m in run(["meeting-list"])["meetings"]}
    assert "m1" in ids and len(ids) >= 2
