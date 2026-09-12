"""Always-on monitor: the autonomous half of ClaimTrace.

Polls SHA-256 hashes of every file under originals/. When an artifact that a
recorded claim depends on changes, it wakes the agent with a one-shot Hermes
turn scoped to the affected claims. No user prompt is involved.

Run inside the sandbox:
    python -m claimtrace.watcher --project project-123

Trigger command (override with CLAIMTRACE_TRIGGER_CMD, {prompt} is substituted):
    hermes -z {prompt} -s claimtrace --yolo

Polling on hashes is deliberate. A filesystem-event library would be fewer
lines but has more ways to be silently wrong during a live demo.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time

from .store import Project, ProjectError, now

DEFAULT_TRIGGER = "hermes -z {prompt} -s claimtrace --yolo"

PROMPT = (
    "Autonomous re-audit for ClaimTrace project {project}. "
    "These source artifacts changed since the last audit: {files}. "
    "Run the incremental re-audit workflow from the claimtrace skill: "
    "identify the claims that cite those files, re-run their verification, "
    "update the claim records, write the report, and emit the sanitized notification. "
    "Do not re-audit unaffected claims."
)


def build_prompt(project_id: str, changed: dict) -> str:
    files = ", ".join(
        sorted(set(changed["added"] + changed["modified"] + changed["removed"]))
    )
    return PROMPT.format(project=project_id, files=files or "none")


def trigger(project: Project, changed: dict, template: str, dry_run: bool) -> dict:
    prompt = build_prompt(project.id, changed)
    command = template.replace("{prompt}", shlex.quote(prompt))
    event = {
        "at": now(),
        "project": project.id,
        "changed": changed,
        "command": command,
        "dry_run": dry_run,
    }
    project.log(
        "change_detected",
        added=len(changed["added"]),
        modified=len(changed["modified"]),
        removed=len(changed["removed"]),
    )
    if dry_run:
        event["result"] = "dry-run, agent not invoked"
        return event

    proc = subprocess.run(command, shell=True, capture_output=True, text=True)  # noqa: S602
    event["exit_code"] = proc.returncode
    event["stderr_tail"] = proc.stderr.strip()[-500:]
    project.log("agent_triggered", exit_code=proc.returncode)
    return event


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="claimtrace.watcher", description="ClaimTrace always-on monitor")
    ap.add_argument("--project", required=True)
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between hash scans")
    ap.add_argument("--trigger", default=None, help="command template, {prompt} is substituted")
    ap.add_argument("--dry-run", action="store_true", help="detect and log, but do not call the agent")
    ap.add_argument("--once", action="store_true", help="scan once and exit")
    ap.add_argument("--quiet-period", type=float, default=2.0,
                    help="seconds a file must stay unchanged before triggering, so a half-written file is not audited")
    args = ap.parse_args(argv)

    import os

    template = args.trigger or os.environ.get("CLAIMTRACE_TRIGGER_CMD", DEFAULT_TRIGGER)

    try:
        project = Project(args.project)
    except ProjectError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "at": now(),
                "event": "watcher_started",
                "project": project.id,
                "watching": str(project.originals),
                "interval_s": args.interval,
                "trigger": template,
                "dry_run": args.dry_run,
            }
        ),
        flush=True,
    )

    pending_since = None
    pending_snapshot = None

    while True:
        try:
            changed = project.changed_files()
            has_changes = any(changed.values())

            if has_changes:
                snapshot = json.dumps(changed, sort_keys=True)
                immediate = args.once or args.quiet_period <= 0
                if not immediate and snapshot != pending_snapshot:
                    # change set is still moving; wait for it to settle
                    pending_snapshot, pending_since = snapshot, time.monotonic()
                elif immediate or time.monotonic() - pending_since >= args.quiet_period:
                    event = trigger(project, changed, template, args.dry_run)
                    print(json.dumps(event), flush=True)
                    if not args.dry_run:
                        # the agent snapshots after a successful re-audit; if it
                        # failed, snapshot anyway so one bad file cannot loop forever
                        if event.get("exit_code", 1) != 0:
                            project.snapshot_hashes()
                            project.log("snapshot_forced_after_failed_trigger")
                    else:
                        project.snapshot_hashes()
                    pending_snapshot, pending_since = None, None
            else:
                pending_snapshot, pending_since = None, None

            if args.once:
                return 0
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print(json.dumps({"at": now(), "event": "watcher_stopped"}), flush=True)
            return 0
        except BrokenPipeError:
            # whatever was reading our log went away; stop quietly
            return 0
        except Exception as exc:  # noqa: BLE001 - a watcher must not die mid-demo
            print(json.dumps({"at": now(), "event": "watcher_error", "error": str(exc)}), flush=True)
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
