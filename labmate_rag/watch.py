"""Host-side re-ingest on change: keeps extracted/, manifest.json and the
search index in step with originals/. Complements labmate/watcher.py, which
decides what the *agent* does about a change; this only refreshes the data.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

from . import config
from .ingest import ingest_project


def _fingerprint(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted((root / "originals").rglob("*")):
        if p.is_file() and not p.name.startswith("."):
            st = p.stat()
            out[p.relative_to(root).as_posix()] = hashlib.sha256(f"{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()
    return out


def watch(project_id: str, interval: float = 5.0, once: bool = False) -> int:
    root = config.projects_root() / project_id
    if not root.is_dir():
        print(json.dumps({"error": f"project '{project_id}' not found at {root}"}), file=sys.stderr)
        return 1
    last = None
    while True:
        current = _fingerprint(root)
        if current != last:
            started = time.monotonic()
            summary = ingest_project(project_id)
            event = {"event": "ingested", "project_id": project_id,
                     "changed": [d["path"] for d in summary["ingested"]], "removed": summary["removed"],
                     "elapsed_s": round(time.monotonic() - started, 1)}
            print(json.dumps(event), flush=True)
            last = current
        if once:
            return 0
        time.sleep(interval)
