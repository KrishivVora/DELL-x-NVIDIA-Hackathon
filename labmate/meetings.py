"""Local meeting register and briefing assembly.

Meetings live in meetings.json (under LABMATE_STATE_ROOT when set, else the project root), edited by the researcher or
by the agent. There is no calendar integration on purpose: a cloud calendar
would put meeting titles and attendee names outside the box, which is exactly
what this product promises not to do.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .store import Project, ProjectError, now


def _load(project: Project) -> dict:
    # The agent's own register wins; a researcher-supplied meetings.json shipped
    # with the (possibly read-only) project is the starting point.
    for path in (project.meetings_path, project.root / "meetings.json"):
        if path.exists():
            return json.loads(path.read_text())
    return {"meetings": []}


def _save(project: Project, data: dict) -> None:
    project.state_dir.mkdir(parents=True, exist_ok=True)
    project.meetings_path.write_text(json.dumps(data, indent=2) + "\n")


def _parse(when: str | None) -> datetime | None:
    if not when:
        return None
    text = when.strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.fromisoformat(text) if fmt is None else datetime.strptime(text, fmt)
        except ValueError:
            continue
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    raise ProjectError(f"could not read '{when}' as a date. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM.")


def list_meetings(project: Project, within_days: float | None = None) -> list[dict]:
    meetings = _load(project)["meetings"]
    for m in meetings:
        dt = _parse(m.get("when"))
        m["_when_dt"] = dt
        m["hours_away"] = (
            round((dt - datetime.now(timezone.utc)).total_seconds() / 3600, 1) if dt else None
        )
    if within_days is not None:
        meetings = [
            m for m in meetings
            if m["hours_away"] is not None and -24 <= m["hours_away"] <= within_days * 24
        ]
    meetings.sort(key=lambda m: (m["_when_dt"] is None, m["_when_dt"] or datetime.max.replace(tzinfo=timezone.utc)))
    for m in meetings:
        m.pop("_when_dt", None)
    return meetings


def upsert(project: Project, meeting: dict) -> dict:
    data = _load(project)
    meetings = data["meetings"]
    if not meeting.get("id"):
        meeting["id"] = f"m-{len(meetings) + 1:03d}"
        while any(m["id"] == meeting["id"] for m in meetings):
            meeting["id"] = f"m-{int(meeting['id'].split('-')[1]) + 1:03d}"
    meeting = {k: v for k, v in meeting.items() if v is not None}
    for i, existing in enumerate(meetings):
        if existing["id"] == meeting["id"]:
            meetings[i] = {**existing, **meeting}
            _save(project, data)
            return meetings[i]
    meeting.setdefault("created_at", now())
    meetings.append(meeting)
    _save(project, data)
    return meeting


def get(project: Project, meeting_id: str) -> dict:
    for m in _load(project)["meetings"]:
        if m["id"] == meeting_id:
            return m
    known = [m["id"] for m in _load(project)["meetings"]]
    raise ProjectError(f"no meeting '{meeting_id}'. Known meetings: {known or 'none'}")


def recent_activity(project: Project, days: float = 7.0) -> dict:
    """Files touched recently, by modification time, plus unaudited changes."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent = []
    if project.originals.is_dir():
        for p in sorted(project.originals.rglob("*")):
            if not p.is_file():
                continue
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            if mtime >= cutoff:
                recent.append(
                    {
                        "path": str(p.relative_to(project.root).as_posix()),
                        "modified_at": mtime.isoformat(timespec="seconds").replace("+00:00", "Z"),
                    }
                )
    recent.sort(key=lambda r: r["modified_at"], reverse=True)
    return {"window_days": days, "recent_files": recent, "unaudited": project.changed_files()}


def brief_context(project: Project, meeting_id: str, k: int = 3, days: float = 7.0) -> dict:
    """Assemble everything a briefing needs. The agent writes the prose."""
    from . import retrieval

    meeting = get(project, meeting_id)
    topics = meeting.get("topics") or [meeting.get("title", "")]

    evidence = []
    for topic in topics:
        if not topic:
            continue
        result = retrieval.retrieve(project, topic, k)
        evidence.append(
            {
                "topic": topic,
                "backend": result["backend"],
                "passages": [
                    {"path": r["path"], "page": r.get("page"), "text": r["text"][:800]}
                    for r in result["results"]
                ],
            }
        )

    open_items = [
        {
            "claim_id": c["claim_id"],
            "claim": c.get("claim"),
            "status": c.get("status"),
            "recommended_action": c.get("recommended_action"),
        }
        for c in project.claims()
        if c.get("status") in ("conflicting", "missing_evidence")
    ]

    return {
        "meeting": {k2: v for k2, v in meeting.items() if not k2.startswith("_")},
        "assembled_at": now(),
        "evidence_by_topic": evidence,
        "activity": recent_activity(project, days),
        "open_items": open_items,
        "previous_brief": meeting.get("brief_path"),
    }


def write_brief(project: Project, meeting_id: str, summary: str, context: dict) -> str:
    meeting = get(project, meeting_id)
    project.reports.mkdir(parents=True, exist_ok=True)
    stamp = now().replace(":", "").replace("-", "")
    path = project.reports / f"brief-{meeting_id}-{stamp}.md"

    lines = [
        f"# Briefing — {meeting.get('title', meeting_id)}",
        "",
        f"When: {meeting.get('when', 'unscheduled')} · "
        f"Attendees: {', '.join(meeting.get('attendees', [])) or 'not recorded'}",
        f"Prepared {now()} · all sources local to this project.",
        "",
        summary.strip(),
        "",
        "## Sources consulted",
        "",
    ]
    for block in context.get("evidence_by_topic", []):
        lines.append(f"**{block['topic']}**")
        lines.append("")
        if not block["passages"]:
            lines.append("- no local source matched this topic")
        for psg in block["passages"]:
            page = f", page {psg['page']}" if psg.get("page") else ""
            lines.append(f"- `{psg['path']}`{page}")
        lines.append("")

    activity = context.get("activity", {})
    if activity.get("recent_files"):
        lines += [
            f"## Changed in the last {activity.get('window_days')} days",
            "",
            *[f"- `{f['path']}` ({f['modified_at']})" for f in activity["recent_files"]],
            "",
        ]
    if context.get("open_items"):
        lines += ["## Open items from earlier analysis", ""]
        for item in context["open_items"]:
            lines.append(f"- **{item['claim_id']}** ({item['status']}) — {item['claim']}")
            if item.get("recommended_action"):
                lines.append(f"  - Suggested: {item['recommended_action']}")
        lines.append("")

    path.write_text("\n".join(lines))
    upsert(project, {"id": meeting_id, "brief_path": str(path), "last_brief_at": now()})
    return str(path)
