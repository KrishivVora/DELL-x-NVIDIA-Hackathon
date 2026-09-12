"""Agent-facing command line. Every tool the ClaimTrace skill is allowed to call.

Each subcommand prints one JSON object to stdout. Errors print
{"error": "..."} and exit 1, so the agent can read the failure and adapt.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import report as report_mod
from . import retrieval, verify
from .store import STATUSES, Project, ProjectError, now

OPS = {
    "csv_stat": verify.csv_stat,
    "csv_unique_count": verify.csv_unique_count,
    "csv_delta": verify.csv_delta,
    "text_search": verify.text_search,
}


def out(obj) -> None:
    json.dump(obj, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


# -- commands ---------------------------------------------------------------


def cmd_project_status(p: Project, a) -> dict:
    docs = p.documents()
    try:
        manuscript = p.manuscript()["path"]
    except ProjectError as exc:
        manuscript = None
        manuscript_error = str(exc)
    else:
        manuscript_error = None
    state = p.state()
    return {
        "project_id": p.id,
        "root": str(p.root),
        "manuscript": manuscript,
        "manuscript_error": manuscript_error,
        "documents": [
            {
                "path": d["path"],
                "role": d.get("role"),
                "artifact_type": d.get("artifact_type"),
                "extraction_status": d.get("extraction_status"),
                "extracted_path": d.get("extracted_path"),
                "pages": d.get("pages"),
            }
            for d in docs
        ],
        "claims_recorded": len(state.get("claims", [])),
        "last_audit_at": state.get("last_audit_at"),
        "changes_since_last_audit": p.changed_files(),
    }


def cmd_read(p: Project, a) -> dict:
    doc = p.document(a.path)
    rel = a.path
    if doc and doc.get("extracted_path") and not a.raw:
        rel = doc["extracted_path"]
    path = p.resolve(rel)
    if not path.is_file():
        raise ProjectError(f"{rel} does not exist in the project")
    text = path.read_text(errors="replace")

    if a.page is not None:
        import re

        blocks = re.split(r"(?m)^<<<PAGE (\d+)>>>$", text)
        # blocks: [pre, pageno, body, pageno, body, ...]
        pages = {int(blocks[i]): blocks[i + 1] for i in range(1, len(blocks) - 1, 2)}
        if a.page not in pages:
            raise ProjectError(
                f"page {a.page} not found in {rel}; pages present: {sorted(pages)[:20]}"
            )
        text = pages[a.page]

    lines = text.splitlines()
    start = max(a.start - 1, 0)
    end = min(start + a.max_lines, len(lines))
    return {
        "path": rel,
        "page": a.page,
        "lines": f"{start + 1}-{end} of {len(lines)}",
        "truncated": end < len(lines),
        "text": "\n".join(lines[start:end]),
    }


def cmd_retrieve(p: Project, a) -> dict:
    return retrieval.retrieve(p, a.query, a.k)


def cmd_inspect_csv(p: Project, a) -> dict:
    path = p.resolve(a.path)
    if not path.is_file():
        raise ProjectError(f"{a.path} does not exist in the project")
    return {"path": a.path, **verify.inspect_csv(path, a.max_rows)}


def cmd_verify(p: Project, a) -> dict:
    args = json.loads(a.args) if a.args else {}
    if a.op not in OPS:
        raise ProjectError(f"unknown op '{a.op}'; expected one of {list(OPS)}")

    if a.op == "text_search":
        paths = [p.resolve(x) for x in (args.pop("paths", None) or _searchable(p))]
        result = verify.text_search(paths, **args)
        result["value"] = float(len(result["hits"]))
        evidence_path = ", ".join(args.get("paths", [])) or "extracted text"
        result.setdefault("description", f"search for {args.get('pattern')!r}")
    else:
        rel = args.pop("path")
        path = p.resolve(rel)
        if not path.is_file():
            raise ProjectError(f"{rel} does not exist in the project")
        result = OPS[a.op](path, **args)
        evidence_path = rel

    payload = {"op": a.op, "path": evidence_path, "args": args, **result}

    if a.reported is not None:
        candidates = {}
        if a.op == "csv_delta":
            candidates["relative percent change"] = result.get("relative_pct_change")
            candidates["absolute percentage-point difference"] = result.get("absolute_point_delta")
            candidates["treatment value itself"] = result.get("treatment_value")
        else:
            candidates["computed value"] = result.get("value")
        payload["comparison"] = verify.compare(
            a.reported, candidates, tolerance=a.tolerance, tolerance_mode=a.tolerance_mode
        )

    if a.claim_id:
        claim = p.claim(a.claim_id)
        if claim is None:
            raise ProjectError(f"claim '{a.claim_id}' does not exist; run claim-set first")
        evidence = claim.get("evidence", [])
        evidence.append(
            {
                "at": now(),
                "op": a.op,
                "path": evidence_path,
                "args": args,
                "description": result.get("description", ""),
                "value": result.get("value"),
            }
        )
        update = {"claim_id": a.claim_id, "evidence": evidence}
        files = set(claim.get("evidence_files", []))
        if a.op != "text_search":
            files.add(evidence_path)
        update["evidence_files"] = sorted(files)
        if "comparison" in payload:
            cmp_ = payload["comparison"]
            update.update(
                {
                    "reported_value": cmp_["reported_value"],
                    "computed_value": cmp_["computed_value"],
                    "status": cmp_["status"],
                    "explanation": cmp_["explanation"],
                }
            )
        p.upsert_claim(update)
        payload["claim_updated"] = a.claim_id

    return payload


def _searchable(p: Project) -> list[str]:
    paths = []
    for d in p.documents():
        if d.get("extracted_path"):
            paths.append(d["extracted_path"])
        elif d["path"].lower().endswith((".txt", ".md", ".yaml", ".yml", ".json", ".py", ".csv")):
            paths.append(d["path"])
    return paths


def cmd_claim_set(p: Project, a) -> dict:
    claim = {
        "claim_id": a.claim_id or p.next_claim_id(),
        "claim": a.claim,
        "manuscript_location": a.location,
        "claim_type": a.type,
    }
    if a.status:
        claim["status"] = a.status
    if a.reported is not None:
        claim["reported_value"] = a.reported
    if a.explanation:
        claim["explanation"] = a.explanation
    if a.action:
        claim["recommended_action"] = a.action
    if a.confidence:
        claim["confidence"] = a.confidence
    if a.evidence_files:
        claim["evidence_files"] = a.evidence_files
    claim = {k: v for k, v in claim.items() if v is not None}
    if a.claim_id and p.claim(a.claim_id) is None and not a.claim:
        raise ProjectError(f"claim '{a.claim_id}' is new, so --claim text is required")
    saved = p.upsert_claim(claim)
    return {"claim": saved}


def cmd_claim_list(p: Project, a) -> dict:
    claims = p.claims()
    if a.status:
        claims = [c for c in claims if c.get("status") == a.status]
    return {"count": len(claims), "claims": claims}


def cmd_changes(p: Project, a) -> dict:
    changed = p.changed_files()
    touched = sorted(set(changed["added"] + changed["removed"] + changed["modified"]))
    affected = [
        {
            "claim_id": c["claim_id"],
            "claim": c.get("claim"),
            "status": c.get("status"),
            "evidence_files": c.get("evidence_files", []),
        }
        for c in p.claims()
        if set(c.get("evidence_files", [])) & set(touched)
    ]
    return {
        "changed": changed,
        "changed_count": len(touched),
        "affected_claims": affected,
        "unaffected_claim_count": len(p.claims()) - len(affected),
    }


def cmd_snapshot(p: Project, a) -> dict:
    p.snapshot_hashes()
    p.log("snapshot", files=len(p.current_hashes()))
    return {"snapshotted_at": p.state()["last_audit_at"], "files": len(p.current_hashes())}


def cmd_report(p: Project, a) -> dict:
    path = report_mod.write(p)
    tally = report_mod.counts(p.claims())
    p.log("report_written", path=Path(path).name)
    return {"report_path": path, "latest": str(p.reports / "latest.md"), "counts": tally}


def cmd_notify(p: Project, a) -> dict:
    """The only text allowed to leave the machine. Counts and pointers only."""
    tally = report_mod.counts(p.claims())
    changed = p.changed_files()
    changed_count = len(changed["added"] + changed["removed"] + changed["modified"])
    reason = a.reason or "audit complete"
    message = (
        f"ClaimTrace · project {p.id} · {reason}: "
        f"{tally['supported']} supported, {tally['conflicting']} conflicting, "
        f"{tally['missing_evidence']} missing evidence, {tally['unverifiable']} unverifiable"
        + (f" · {changed_count} source file(s) changed" if changed_count else "")
        + f" · details in the local report at {p.reports / 'latest.md'}"
    )
    return {"message": message, "contains_research_content": False}


def cmd_log(p: Project, a) -> dict:
    p.log(a.event, **dict(kv.split("=", 1) for kv in a.field))
    return {"logged": a.event}


# -- parser -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="claimtrace", description="ClaimTrace deterministic tools")
    ap.add_argument("--project", required=True, help="project id under CLAIMTRACE_PROJECTS_ROOT")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("project-status", help="manifest summary, manuscript, pending changes")

    r = sub.add_parser("read", help="read bounded text from a document")
    r.add_argument("--path", required=True)
    r.add_argument("--page", type=int)
    r.add_argument("--start", type=int, default=1)
    r.add_argument("--max-lines", type=int, default=120)
    r.add_argument("--raw", action="store_true", help="read the original, not the extracted text")

    q = sub.add_parser("retrieve", help="find evidence candidates (not verification)")
    q.add_argument("--query", required=True)
    q.add_argument("-k", type=int, default=5)

    i = sub.add_parser("inspect-csv", help="columns, dtypes and head of a CSV")
    i.add_argument("--path", required=True)
    i.add_argument("--max-rows", type=int, default=5)

    v = sub.add_parser("verify", help="run one deterministic computation")
    v.add_argument("--op", required=True, choices=list(OPS))
    v.add_argument("--args", default="{}", help="JSON object of op arguments")
    v.add_argument("--reported", type=float, help="value the manuscript states")
    v.add_argument("--tolerance", type=float, default=0.5)
    v.add_argument("--tolerance-mode", choices=["absolute", "relative"], default="absolute")
    v.add_argument("--claim-id", help="record this calculation on a claim")

    c = sub.add_parser("claim-set", help="create or update a claim record")
    c.add_argument("--claim-id")
    c.add_argument("--claim")
    c.add_argument("--location", help="e.g. 'Results, page 7'")
    c.add_argument(
        "--type", choices=["quantitative", "experimental", "methodological", "interpretive"]
    )
    c.add_argument("--status", choices=list(STATUSES))
    c.add_argument("--reported", type=float)
    c.add_argument("--explanation")
    c.add_argument("--action", "--recommended-action", dest="action", help="recommended action")
    c.add_argument("--confidence", choices=["high", "medium", "low"])
    c.add_argument("--evidence-files", nargs="*")

    cl = sub.add_parser("claim-list", help="list recorded claims")
    cl.add_argument("--status", choices=list(STATUSES))

    sub.add_parser("changes", help="files changed since last snapshot and the claims they affect")
    sub.add_parser("snapshot", help="record current file hashes as the audited baseline")
    sub.add_parser("report", help="write the local markdown report")

    n = sub.add_parser("notify", help="sanitized one-line summary safe for Slack")
    n.add_argument("--reason")

    lg = sub.add_parser("log", help="append a metadata-only audit event")
    lg.add_argument("--event", required=True)
    lg.add_argument("--field", nargs="*", default=[])

    return ap


HANDLERS = {
    "project-status": cmd_project_status,
    "read": cmd_read,
    "retrieve": cmd_retrieve,
    "inspect-csv": cmd_inspect_csv,
    "verify": cmd_verify,
    "claim-set": cmd_claim_set,
    "claim-list": cmd_claim_list,
    "changes": cmd_changes,
    "snapshot": cmd_snapshot,
    "report": cmd_report,
    "notify": cmd_notify,
    "log": cmd_log,
}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        project = Project(args.project)
        out(HANDLERS[args.cmd](project, args))
        return 0
    except (ProjectError, verify.VerifyError) as exc:
        out({"error": str(exc)})
        return 1
    except Exception as exc:  # noqa: BLE001 - the agent needs the failure, not a traceback
        out({"error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
