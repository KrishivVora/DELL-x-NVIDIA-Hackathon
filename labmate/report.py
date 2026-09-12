"""Markdown audit report. Local only; never sent to Slack."""

from __future__ import annotations

from .store import Project, now

STATUS_LABEL = {
    "supported": "Supported",
    "conflicting": "Conflict",
    "missing_evidence": "Missing evidence",
    "unverifiable": "Unverifiable",
}
SEVERITY_ORDER = ["conflicting", "missing_evidence", "unverifiable", "supported"]


def counts(claims: list[dict]) -> dict:
    out = {s: 0 for s in STATUS_LABEL}
    for c in claims:
        out[c.get("status", "unverifiable")] = out.get(c.get("status", "unverifiable"), 0) + 1
    return out


def render(project: Project) -> str:
    state = project.state()
    claims = state.get("claims", [])
    tally = counts(claims)
    try:
        manuscript = project.manuscript()["path"]
    except Exception:  # noqa: BLE001 - report should render even without a clean manifest
        manuscript = "unidentified"

    lines = [
        f"# ClaimTrace audit — {project.id}",
        "",
        f"Generated {now()} · manuscript `{manuscript}` · all inference and evidence local.",
        "",
        "## Summary",
        "",
        f"- **{tally['supported']}** supported",
        f"- **{tally['conflicting']}** conflicting",
        f"- **{tally['missing_evidence']}** missing evidence",
        f"- **{tally['unverifiable']}** unverifiable",
        "",
        "## Claim to evidence matrix",
        "",
        "| Claim | Location | Evidence | Verification | Status |",
        "|---|---|---|---|---|",
    ]

    ordered = sorted(claims, key=lambda c: SEVERITY_ORDER.index(c.get("status", "unverifiable")))
    for c in ordered:
        evidence = ", ".join(f"`{p}`" for p in c.get("evidence_files", [])) or "_none found_"
        reported, computed = c.get("reported_value"), c.get("computed_value")
        if reported is not None and computed is not None:
            check = f"reported {reported:g} vs computed {computed:g}"
        elif computed is not None:
            check = f"computed {computed:g}"
        else:
            check = c.get("verification_note", "not computable")
        claim_text = c.get("claim", "").replace("|", "\\|")
        lines.append(
            f"| {claim_text} | {c.get('manuscript_location', '—')} | {evidence} "
            f"| {check} | **{STATUS_LABEL.get(c.get('status'), c.get('status'))}** |"
        )

    lines += ["", "## Findings by severity", ""]
    for status in SEVERITY_ORDER:
        group = [c for c in ordered if c.get("status") == status]
        if not group:
            continue
        lines.append(f"### {STATUS_LABEL[status]} ({len(group)})")
        lines.append("")
        for c in group:
            lines.append(f"- **{c.get('claim_id')}** — {c.get('claim')}")
            lines.append(f"  - Location: {c.get('manuscript_location', '—')}")
            if c.get("explanation"):
                lines.append(f"  - Finding: {c['explanation']}")
            if c.get("recommended_action"):
                lines.append(f"  - Action: {c['recommended_action']}")
            for ev in c.get("evidence", []):
                lines.append(
                    f"  - Calculation: `{ev.get('op')}` on `{ev.get('path')}` → "
                    f"{ev.get('description', '')}"
                )
        lines.append("")

    docs = project.documents()
    unreadable = [d for d in docs if d.get("extraction_status") in ("failed", "partial")]
    lines += [
        "## Files inspected",
        "",
        *[f"- `{d['path']}` ({d.get('artifact_type', 'unknown')})" for d in docs],
        "",
    ]
    if unreadable:
        lines += [
            "## Files not fully readable",
            "",
            *[f"- `{d['path']}` — extraction {d.get('extraction_status')}" for d in unreadable],
            "",
        ]

    history = state.get("history", [])[-10:]
    if history:
        lines += ["## Recent audit events", ""]
        lines += [
            f"- {h['at']} — {h['event']}"
            + (f" ({', '.join(f'{k}={v}' for k, v in h.items() if k not in ('at', 'event'))})" if len(h) > 2 else "")
            for h in history
        ]
        lines.append("")

    return "\n".join(lines)


def write(project: Project) -> str:
    project.reports.mkdir(parents=True, exist_ok=True)
    stamp = now().replace(":", "").replace("-", "")
    path = project.reports / f"audit-{stamp}.md"
    text = render(project)
    path.write_text(text)
    latest = project.reports / "latest.md"
    latest.write_text(text)
    return str(path)
