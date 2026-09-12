"""Project paths and persistence for manifest, claims, and audit state.

Everything the agent writes goes through here so the on-disk shape stays
consistent with CONTRACT.md. Originals are never written.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

STATUSES = ("supported", "conflicting", "missing_evidence", "unverifiable")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def projects_root() -> Path:
    return Path(os.environ.get("CLAIMTRACE_PROJECTS_ROOT", "projects")).expanduser()


class ProjectError(RuntimeError):
    pass


class Project:
    def __init__(self, project_id: str):
        self.id = project_id
        self.root = projects_root() / project_id
        if not self.root.is_dir():
            raise ProjectError(
                f"project '{project_id}' not found at {self.root}. "
                f"Set CLAIMTRACE_PROJECTS_ROOT or check the project id."
            )

    # -- paths ---------------------------------------------------------------

    @property
    def originals(self) -> Path:
        return self.root / "originals"

    @property
    def extracted(self) -> Path:
        return self.root / "extracted"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def state_path(self) -> Path:
        return self.root / "audit-state.json"

    def resolve(self, rel: str) -> Path:
        """Resolve a project-relative path, refusing escapes outside the project."""
        p = (self.root / rel).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ProjectError(f"path escapes project root: {rel}")
        return p

    # -- manifest ------------------------------------------------------------

    def manifest(self) -> dict:
        if not self.manifest_path.exists():
            raise ProjectError(
                f"no manifest.json in {self.root}. Ingestion has not run for this project yet."
            )
        return json.loads(self.manifest_path.read_text())

    def documents(self) -> list[dict]:
        return self.manifest().get("documents", [])

    def manuscript(self) -> dict:
        docs = self.documents()
        primary = [d for d in docs if d.get("role") == "manuscript"]
        if len(primary) == 1:
            return primary[0]
        if len(primary) > 1:
            raise ProjectError(
                "manifest declares more than one manuscript: "
                + ", ".join(d["path"] for d in primary)
            )
        pdfs = [d for d in docs if d.get("path", "").lower().endswith(".pdf")]
        if len(pdfs) == 1:
            return pdfs[0]
        raise ProjectError(
            "cannot identify the primary manuscript. Set role='manuscript' in manifest.json."
        )

    def document(self, path: str) -> dict | None:
        for d in self.documents():
            if d.get("path") == path:
                return d
        return None

    # -- audit state ---------------------------------------------------------

    def state(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {
            "project_id": self.id,
            "last_audit_at": None,
            "file_hashes": {},
            "claims": [],
            "history": [],
        }

    def write_state(self, state: dict) -> None:
        state["project_id"] = self.id
        self.state_path.write_text(json.dumps(state, indent=2) + "\n")

    def claims(self) -> list[dict]:
        return self.state().get("claims", [])

    def claim(self, claim_id: str) -> dict | None:
        for c in self.claims():
            if c.get("claim_id") == claim_id:
                return c
        return None

    def upsert_claim(self, claim: dict) -> dict:
        state = self.state()
        claims = state.setdefault("claims", [])
        claim["updated_at"] = now()
        for i, existing in enumerate(claims):
            if existing.get("claim_id") == claim.get("claim_id"):
                merged = {**existing, **claim}
                # evidence accumulates rather than being clobbered
                if "evidence" in existing and "evidence" not in claim:
                    merged["evidence"] = existing["evidence"]
                claims[i] = merged
                self.write_state(state)
                return merged
        claim.setdefault("created_at", claim["updated_at"])
        claims.append(claim)
        self.write_state(state)
        return claim

    def next_claim_id(self) -> str:
        n = len(self.claims()) + 1
        while self.claim(f"claim-{n:03d}"):
            n += 1
        return f"claim-{n:03d}"

    def log(self, event: str, **fields) -> None:
        """Append a metadata-only audit event. Never log document content."""
        state = self.state()
        state.setdefault("history", []).append({"at": now(), "event": event, **fields})
        self.write_state(state)

    # -- hashing -------------------------------------------------------------

    def current_hashes(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if not self.originals.is_dir():
            return out
        for p in sorted(self.originals.rglob("*")):
            if p.is_file():
                out[str(p.relative_to(self.root).as_posix())] = sha256_file(p)
        return out

    def changed_files(self) -> dict[str, list[str]]:
        prev = self.state().get("file_hashes", {})
        cur = self.current_hashes()
        return {
            "added": sorted(set(cur) - set(prev)),
            "removed": sorted(set(prev) - set(cur)),
            "modified": sorted(p for p in set(cur) & set(prev) if cur[p] != prev[p]),
        }

    def snapshot_hashes(self) -> None:
        state = self.state()
        state["file_hashes"] = self.current_hashes()
        state["last_audit_at"] = now()
        self.write_state(state)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()
