"""Scheduled autonomy: the clock-driven half of the always-on agent.

The watcher reacts to file changes. This reacts to *time* and to standing
backlog. Each tick it runs cheap, deterministic checks (no model) to build a
ranked list of pending work, and only when the top item clears its rate limit
does it wake the agent to do that one thing. A quiet tick costs no GPU turn and
is logged as quiet on purpose — on a demo screen that honesty reads better than
inventing busywork.

Run inside the sandbox:
    python -m labmate.scheduler --project project-123 --interval 60

The agent, not this module, decides how to do the work. This module only
decides *whether* there is work worth waking it for, and *which* is most urgent.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time

from . import meetings as meetings_mod
from .store import Project, ProjectError, now

DEFAULT_TRIGGER = "hermes -z {prompt} -s research-assistant -s meeting-prep -s claimtrace --yolo"

# How often each kind of work may wake the agent, in seconds. Urgent work is
# effectively unthrottled; routine maintenance is rate-limited so the agent
# does not spin re-doing the same low-value sweep. Override any of these with
# LABMATE_RATELIMIT_<KIND>=<seconds>.
RATE_LIMITS = {
    "meeting_brief": 0,
    "reverify": 0,
    "verify_new": 15 * 60,
    "research_missing": 60 * 60,
    "goals_refresh": 6 * 60 * 60,
    "digest": 12 * 60 * 60,
}

# Higher runs first.
PRIORITY = {
    "reverify": 100,
    "meeting_brief": 90,
    "verify_new": 60,
    "goals_refresh": 40,
    "research_missing": 30,
    "digest": 10,
}

STATUSES_DONE = {"supported", "conflicting", "missing_evidence", "unverifiable"}


def _rate_limit(kind: str) -> float:
    override = os.environ.get(f"LABMATE_RATELIMIT_{kind.upper()}")
    if override is not None:
        try:
            return float(override)
        except ValueError:
            pass
    return RATE_LIMITS.get(kind, 0)


def _last_run(project: Project) -> dict:
    return project.state().get("scheduler", {}).get("last_run", {})


def _record_run(project: Project, kind: str) -> None:
    state = project.state()
    sched = state.setdefault("scheduler", {}).setdefault("last_run", {})
    sched[kind] = time.time()
    project.write_state(state)


def pending_work(project: Project, meeting_window_hours: float = 3.0) -> list[dict]:
    """Cheap deterministic scan for work worth doing. No model involved.

    Returns every candidate, ranked, with an `eligible` flag reflecting its
    rate limit, so a dry run can show the whole backlog and why items are held.
    """
    items: list[dict] = []
    claims = project.claims()

    # URGENT: a source file changed and a recorded claim depends on it.
    changed = project.changed_files()
    touched = set(changed["added"] + changed["removed"] + changed["modified"])
    affected = [
        c for c in claims if set(c.get("evidence_files", [])) & touched
    ]
    if affected:
        items.append(
            {
                "kind": "reverify",
                "summary": f"{len(affected)} claim(s) cite {len(touched)} changed file(s)",
                "detail": {
                    "changed": sorted(touched),
                    "claim_ids": [c["claim_id"] for c in affected],
                },
            }
        )

    # URGENT: a meeting is imminent and has no brief.
    try:
        soon = meetings_mod.list_meetings(project, within_days=meeting_window_hours / 24.0)
    except ProjectError:
        soon = []
    for m in soon:
        if m.get("hours_away") is not None and 0 <= m["hours_away"] <= meeting_window_hours \
                and not m.get("brief_path"):
            items.append(
                {
                    "kind": "meeting_brief",
                    "summary": f"meeting '{m.get('title', m['id'])}' in "
                    f"{m['hours_away']}h has no brief",
                    "detail": {"meeting_id": m["id"]},
                }
            )

    # ROUTINE: claims recorded but never verified.
    unverified = [c for c in claims if c.get("status") not in STATUSES_DONE]
    if unverified:
        items.append(
            {
                "kind": "verify_new",
                "summary": f"{len(unverified)} claim(s) recorded but not yet verified",
                "detail": {"claim_ids": [c["claim_id"] for c in unverified]},
            }
        )

    # ROUTINE: missing-evidence claims worth re-searching in case new files help.
    missing = [c for c in claims if c.get("status") == "missing_evidence"]
    if missing:
        items.append(
            {
                "kind": "research_missing",
                "summary": f"re-search evidence for {len(missing)} unsupported claim(s)",
                "detail": {"claim_ids": [c["claim_id"] for c in missing]},
            }
        )

    # ROUTINE: goals exist and may need a progress reassessment. Optional —
    # present only once the goals capability lands; harmless until then.
    if (project.root / "goals.json").exists():
        items.append(
            {
                "kind": "goals_refresh",
                "summary": "reassess progress against recorded goals",
                "detail": {},
            }
        )

    # FLOOR: a periodic digest, so a genuinely quiet project still gets a
    # heartbeat summary rather than total silence.
    items.append(
        {
            "kind": "digest",
            "summary": "periodic project digest",
            "detail": {},
        }
    )

    last = _last_run(project)
    for it in items:
        it["priority"] = PRIORITY.get(it["kind"], 0)
        limit = _rate_limit(it["kind"])
        since = time.time() - last.get(it["kind"], 0)
        it["rate_limit_s"] = limit
        it["eligible"] = since >= limit
        it["held_for_s"] = round(max(0.0, limit - since), 1) if not it["eligible"] else 0.0
    items.sort(key=lambda it: (it["eligible"], it["priority"]), reverse=True)
    return items


PROMPTS = {
    "reverify": (
        "Scheduled check, project {project}. These files changed: {changed}. "
        "Using the claimtrace skill, re-run the recorded verification for claims "
        "{claim_ids} and report anything whose status changed. Do not touch other claims."
    ),
    "meeting_brief": (
        "Scheduled check, project {project}. Meeting {meeting_id} is coming up and has no "
        "brief. Using the meeting-prep skill, assemble and write its briefing now, then "
        "emit the sanitized notification that it is ready."
    ),
    "verify_new": (
        "Scheduled check, project {project}. Claims {claim_ids} are recorded but not yet "
        "verified. Using the claimtrace skill, verify each against the project's data and "
        "record the result."
    ),
    "research_missing": (
        "Scheduled check, project {project}. Claims {claim_ids} were marked missing-evidence "
        "earlier. Using the research-assistant and claimtrace skills, search the current "
        "files again; if evidence now exists, verify and update the claim, otherwise leave it."
    ),
    "goals_refresh": (
        "Scheduled check, project {project}. Reassess progress against the recorded goals "
        "using current files, update each goal's status, and flag any goal with no recent "
        "supporting activity."
    ),
    "digest": (
        "Scheduled digest, project {project}. Run `labmate --project {project} digest`, then "
        "summarise what changed, what is unresolved, and what is coming up. If nothing is "
        "worth reporting, say so briefly and stop. Finish with the sanitized notification."
    ),
}


def build_prompt(project_id: str, item: dict) -> str:
    detail = item.get("detail", {})
    return PROMPTS[item["kind"]].format(
        project=project_id,
        changed=", ".join(detail.get("changed", [])) or "none",
        claim_ids=", ".join(detail.get("claim_ids", [])) or "none",
        meeting_id=detail.get("meeting_id", ""),
    )


def tick(project: Project, template: str, dry_run: bool, meeting_window_hours: float) -> dict:
    backlog = pending_work(project, meeting_window_hours)
    eligible = [it for it in backlog if it["eligible"]]
    event = {
        "at": now(),
        "project": project.id,
        "backlog": [
            {"kind": it["kind"], "summary": it["summary"],
             "eligible": it["eligible"], "held_for_s": it["held_for_s"]}
            for it in backlog
        ],
    }

    # "digest" is the floor; treat a tick where only digest is eligible and it
    # is not yet due as quiet, so the agent is not woken merely to say nothing.
    actionable = [it for it in eligible if it["kind"] != "digest"] or eligible
    if not actionable:
        event["action"] = "quiet"
        return event

    chosen = actionable[0]
    prompt = build_prompt(project.id, chosen)
    command = template.replace("{prompt}", shlex.quote(prompt))
    event["action"] = chosen["kind"]
    event["reason"] = chosen["summary"]
    event["command"] = command
    event["dry_run"] = dry_run
    project.log("scheduler_wake", kind=chosen["kind"], reason=chosen["summary"])

    if not dry_run:
        proc = subprocess.run(command, shell=True, capture_output=True, text=True)  # noqa: S602
        event["exit_code"] = proc.returncode
        event["stderr_tail"] = proc.stderr.strip()[-500:]
    _record_run(project, chosen["kind"])
    return event


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="labmate.scheduler", description="Labmate scheduled autonomy")
    ap.add_argument("--project", required=True)
    ap.add_argument("--interval", type=float, default=60.0, help="seconds between ticks")
    ap.add_argument("--trigger", default=None, help="command template, {prompt} is substituted")
    ap.add_argument("--meeting-window-hours", type=float, default=3.0,
                    help="how far ahead a meeting triggers an auto-brief")
    ap.add_argument("--dry-run", action="store_true", help="rank the backlog and log, but do not call the agent")
    ap.add_argument("--once", action="store_true", help="run one tick and exit")
    args = ap.parse_args(argv)

    template = args.trigger or os.environ.get("LABMATE_TRIGGER_CMD", DEFAULT_TRIGGER)

    try:
        project = Project(args.project)
    except ProjectError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps({
        "at": now(), "event": "scheduler_started", "project": project.id,
        "interval_s": args.interval, "trigger": template, "dry_run": args.dry_run,
    }), flush=True)

    while True:
        try:
            print(json.dumps(tick(project, template, args.dry_run, args.meeting_window_hours)), flush=True)
            if args.once:
                return 0
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print(json.dumps({"at": now(), "event": "scheduler_stopped"}), flush=True)
            return 0
        except BrokenPipeError:
            return 0
        except Exception as exc:  # noqa: BLE001 - a scheduler must not die mid-demo
            print(json.dumps({"at": now(), "event": "scheduler_error", "error": str(exc)}), flush=True)
            if args.once:
                return 1
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
