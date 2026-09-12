"""Bring a file shared in Slack into a local project.

A Slack upload is not something the agent reads directly — and it should not be,
because the file has already transited Slack's cloud by the time the bot sees
it. This module makes the upload *become a local file*: download it into the
project's originals/, ingest it, and let the existing pipeline take over. The
watcher then detects the new file on its own, so no other code changes.

Call it from the Slack bridge handler when a file is shared:

    from labmate.slack_intake import intake_slack_file
    intake_slack_file(project_id, url_private_download, filename,
                      token=os.environ["SLACK_BOT_TOKEN"])

Or from the command line (token comes from the environment, never argv):

    python -m labmate.slack_intake --project P --url <url_private_download> \
        --name results.csv --token-env SLACK_BOT_TOKEN
    python -m labmate.slack_intake --project P --local-file ./results.csv   # test path

Security: the token is read from the environment only. Downloaded files are
treated as untrusted data — saved, never executed. Enforce an extension
allowlist and a size cap so a hostile or accidental upload cannot fill the disk.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from . import ingest
from .store import Project, ProjectError

# What a researcher legitimately shares. Keep it tight; expand deliberately.
ALLOWED_SUFFIXES = {
    ".pdf", ".csv", ".tsv", ".txt", ".md", ".json", ".yaml", ".yml",
    ".py", ".ipynb", ".tex",
}
DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB


class IntakeError(RuntimeError):
    pass


def _max_bytes() -> int:
    try:
        return int(os.environ.get("LABMATE_MAX_UPLOAD_BYTES", DEFAULT_MAX_BYTES))
    except ValueError:
        return DEFAULT_MAX_BYTES


def _check_name(filename: str) -> str:
    name = os.path.basename(filename or "").strip()
    if not name or name.startswith("."):
        raise IntakeError(f"unsafe or empty filename: {filename!r}")
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise IntakeError(
            f"file type '{suffix or '(none)'}' is not allowed. "
            f"Allowed: {sorted(ALLOWED_SUFFIXES)}"
        )
    return name


def _download(url: str, token: str | None, limit: int) -> bytes:
    req = urllib.request.Request(url)
    if token:
        # Slack's url_private(_download) requires the bot token as a bearer header.
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - host is allow-listed by OpenShell policy
            ctype = resp.headers.get("Content-Type", "")
            # Slack returns an HTML login page (200) when the token is missing or
            # wrong, instead of the file. Catch that so it is not ingested as data.
            if "text/html" in ctype:
                raise IntakeError(
                    "Slack returned an HTML page, not the file. The bot token is "
                    "probably missing the files:read scope, or the URL is not the "
                    "url_private_download for a file the bot can access."
                )
            data = resp.read(limit + 1)
    except urllib.error.HTTPError as exc:
        raise IntakeError(f"download failed: HTTP {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise IntakeError(
            f"download failed: {exc.reason}. If the sandbox is deny-all, the Slack "
            f"file host must be allow-listed in the OpenShell policy."
        ) from exc
    if len(data) > limit:
        raise IntakeError(f"file exceeds the {limit} byte limit")
    return data


def intake_slack_file(
    project_id: str,
    url: str,
    filename: str,
    token: str | None = None,
    role: str = "evidence",
) -> dict:
    """Download a Slack file into the project and ingest it. Returns a summary."""
    project = Project(project_id)
    name = _check_name(filename)
    data = _download(url, token, _max_bytes())

    # write to a temp file first so a failed ingest never leaves a half file in
    # originals/ for the watcher to trip over
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(name).suffix) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        staged = tmp_path.parent / name
        tmp_path.replace(staged)
        record = ingest.ingest_path(project, staged, role=role)
    finally:
        for p in (tmp_path, tmp_path.parent / name):
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass

    return {
        "project_id": project_id,
        "ingested": record["path"],
        "bytes": len(data),
        "extraction_status": record["extraction_status"],
        "message": f"Added {name} to project {project_id}. It is now a local file; "
        f"ask about it or audit it by project id. The file is stored locally only.",
    }


def intake_local_file(project_id: str, path: str, role: str = "evidence") -> dict:
    """Ingest a file already on disk. The test path, and useful from a host mount."""
    project = Project(project_id)
    _check_name(os.path.basename(path))
    record = ingest.ingest_path(project, Path(path), role=role)
    return {
        "project_id": project_id,
        "ingested": record["path"],
        "extraction_status": record["extraction_status"],
        "message": f"Ingested {record['path']} into project {project_id}.",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="labmate.slack_intake", description="Bring a Slack upload into a project")
    ap.add_argument("--project", required=True)
    ap.add_argument("--url", help="Slack url_private_download of the file")
    ap.add_argument("--name", help="filename (required with --url)")
    ap.add_argument("--token-env", default="SLACK_BOT_TOKEN",
                    help="env var holding the Slack bot token (never pass the token on argv)")
    ap.add_argument("--local-file", help="ingest a file already on disk instead of downloading")
    ap.add_argument("--role", default="evidence", choices=["evidence", "manuscript"])
    args = ap.parse_args(argv)

    try:
        if args.local_file:
            result = intake_local_file(args.project, args.local_file, args.role)
        else:
            if not args.url or not args.name:
                raise IntakeError("--url and --name are required (or use --local-file)")
            token = os.environ.get(args.token_env)
            result = intake_slack_file(args.project, args.url, args.name, token=token, role=args.role)
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    except (IntakeError, ProjectError, FileNotFoundError) as exc:
        json.dump({"error": str(exc)}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
