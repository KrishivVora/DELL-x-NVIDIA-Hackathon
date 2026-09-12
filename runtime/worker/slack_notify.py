"""Slack notifications for the ClaimTrace worker, sent through the sandboxed Hermes agent.

Design (plan §9, §16.5, §17.2, §19)
-----------------------------------
Hermes Agent 0.20.6 deliberately has no ``send_message`` tool, so the agent cannot start
a Slack message on its own. Every unprompted post (alerts, audit results, digests) is
therefore sent by the host worker, which shells out to ``hermes send`` *inside* the
sandbox through ``nemohermes <sandbox> exec``. The Slack tokens live only in the
sandbox's OpenShell credential store; the host never sees them.

This module is stdlib-only and does three things:

1. **Formats** the three message kinds (``alert``, ``audit_result``, ``digest``).
   The default (``metadata_only=False``) is the detailed mode the demo needs: it shows
   the reported and computed values. ``metadata_only=True`` is the plan §16.5 option
   for strict customers: IDs, statuses, counts, file paths and times, never claim
   text, values or explanations.
2. **Sends** with ``subprocess.run`` (argument list, never ``shell=True``), with a
   timeout and retries, and cleans/redacts the captured output.
3. **Records** each send in the ``notifications`` collection (plan §14) and dedupes on
   ``(project_id, kind, target, sorted event_ids)`` so a status change is posted once.

Verified CLI contract (read-only ``--help`` on this box, 2026-09-12)
------------------------------------------------------------------
``nemohermes my-hermes exec --help`` (NemoClaw v0.0.123):

* ``nemohermes my-hermes exec [--workdir <dir>] [--tty|--no-tty] [--timeout <s>]
  [--stdin|--no-stdin] -- <cmd> [args...]``
* "exits with the remote command's exit code"; "arguments after ``--`` preserve embedded
  line endings and quotes" (multi-line text can be passed as one positional argument).
* "NUL bytes are rejected"; stdin is inherited only when it is a terminal (pass
  ``--stdin`` to forward a pipe); no pseudo-terminal unless ``--tty``, so stdout and
  stderr stay separate.
* Before the command output the CLI prints ``✓ Active gateway set to 'nemoclaw'`` with
  ANSI colour codes; :func:`clean_cli_output` strips both.

``nemohermes my-hermes exec -- hermes send --help`` (Hermes Agent 0.20.6,
``/opt/hermes/hermes_cli/send_cmd.py``):

* ``hermes send [-h] [-t TARGET] [-f PATH] [-s LINE] [-l] [-q] [--json] [message]``
* Message text is the positional ``message``; if omitted it is read from ``--file PATH``
  (``-`` forces stdin) or piped stdin.
* ``--to``: ``platform``, ``platform:chat_id``, ``platform:chat_id:thread_id`` or
  ``platform:#channel-name`` (example ``slack:C0123ABCD``). A Slack thread target is
  therefore ``slack:<channel_id>:<thread_ts>``.
* ``--list [platform]`` lists configured targets; ``--json`` emits a raw JSON result;
  ``-q`` suppresses stdout on success.
* ``MEDIA:<path>`` inside the message text turns it into an attachment upload, so
  outbound text must never contain a bare ``MEDIA:`` token (see :func:`neutralize_media`).
* "Exit codes: 0 ok, 1 delivery/backend error, 2 usage error."
* No per-platform length limit was found in ``send_cmd.py``; Slack itself accepts about
  40 000 characters but renders best under ~4 000, so :data:`DEFAULT_MAX_LEN` is 3 500.

Resulting command (see :func:`build_send_argv`)::

    nemohermes my-hermes exec --timeout 30 -- hermes send --to slack:C0123ABCD --json <text>

Slack is not configured yet on this box; nothing in this module is executed for real by
the tests, which use a fake runner and an in-memory ``notifications`` collection.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol, Sequence

__all__ = [
    "AlertEvent",
    "AuditRun",
    "Claim",
    "Digest",
    "Finding",
    "SendResult",
    "SlackNotifier",
    "build_send_argv",
    "build_list_argv",
    "clean_cli_output",
    "format_alert",
    "format_audit_result",
    "format_digest",
    "neutralize_media",
    "redact_tokens",
    "resolve_nemohermes_bin",
    "slack_target",
    "truncate_text",
]

logger = logging.getLogger("claimtrace.slack_notify")

KIND_ALERT = "alert"
KIND_AUDIT_RESULT = "audit_result"
KIND_DIGEST = "digest"
KINDS = (KIND_ALERT, KIND_AUDIT_RESULT, KIND_DIGEST)

#: Characters of message text sent to Slack before truncation (see module docstring).
DEFAULT_MAX_LEN = 3500
TRUNCATION_NOTE = "… (truncated; full details in the ClaimTrace dashboard)"
DASHBOARD_HINT = "Values and explanation are in the ClaimTrace dashboard."

#: Statuses in display order for count lines. Any other status seen is appended.
KNOWN_STATUSES = ("supported", "conflicting", "unsupported", "missing_evidence")
#: Claims whose status is not one of these "need attention" in audit results.
OK_STATUSES = frozenset({"supported"})

_STATUS_EMOJI = {
    "conflicting": "⚠️",
    "unsupported": "❌",
    "missing_evidence": "❓",
    "supported": "✅",
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_GATEWAY_LINE_RE = re.compile(r"^\s*\S*\s*Active gateway set to .*$", re.MULTILINE)
# Slack bot/user/app/refresh tokens: xoxb-, xoxp-, xoxa-, xoxr-, xoxe-, xoxs- and xapp-.
_TOKEN_RE = re.compile(r"\b(?:xox[a-z]|xapp)-[A-Za-z0-9-]+")
_MEDIA_RE = re.compile(r"MEDIA:", re.IGNORECASE)
_NVM_GLOB = ".nvm/versions/node/*/bin/nemohermes"


# --------------------------------------------------------------------------------------
# Input records (field names follow the MongoDB data model, plan §14 / §15.4)
# --------------------------------------------------------------------------------------


@dataclass
class AlertEvent:
    """An ``audit_events`` document (plan §14) plus the claim fields an alert may show.

    ``cause`` is ``{"path": ..., "old_sha256": ..., "new_sha256": ...}``. The value
    fields are only rendered when ``metadata_only=False``.
    """

    project_id: str
    claim_id: str
    from_status: str
    to_status: str
    cause: Mapping[str, Any] = field(default_factory=dict)
    at: Any = None  # datetime or ISO-8601 string
    run_id: Optional[str] = None
    event_id: Optional[str] = None
    # Detailed-mode extras (from the ``claims`` document):
    reported_value: Any = None
    computed_value: Any = None
    unit: Optional[str] = None
    claim: Optional[str] = None
    explanation: Optional[str] = None


@dataclass
class AuditRun:
    """An ``audit_runs`` document (plan §14)."""

    project_id: str
    trigger: str = "manual"  # manual | slack | file_change | scheduled_sweep
    changed_artifacts: Sequence[str] = ()
    claims_checked: Optional[int] = None
    run_id: Optional[str] = None
    started_at: Any = None
    finished_at: Any = None
    status: Optional[str] = None
    errors: Sequence[Any] = ()


@dataclass
class Claim:
    """The subset of a ``claims`` document (plan §15.4) used in audit results."""

    claim_id: str
    status: str
    evidence: Sequence[Mapping[str, Any]] = ()  # [{"path": ..., "locator": ...}]
    reported_value: Any = None
    computed_value: Any = None
    unit: Optional[str] = None
    claim: Optional[str] = None
    explanation: Optional[str] = None


@dataclass
class Finding:
    """One entry of ``digests.findings`` (plan §17.3 output contract)."""

    claim_id: str
    kind: str  # new_evidence | stale_result | ...
    paths: Sequence[str] = ()
    note: Optional[str] = None


@dataclass
class Digest:
    """A ``digests`` document (plan §14) with the optional ``claims_checked`` count."""

    project_id: str
    findings: Sequence[Any] = ()  # Finding or mapping
    created_at: Any = None
    hermes_job_id: Optional[str] = None
    summary_md: Optional[str] = None
    claims_checked: Optional[int] = None
    digest_id: Optional[str] = None


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a mapping or an attribute, so dicts and dataclasses both work."""
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _to_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _fmt_clock(value: Any) -> str:
    """``2:02 PM`` in local time (aware datetimes are converted, naive ones used as-is)."""
    dt = _to_datetime(value)
    if dt is None:
        return "an unknown time"
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def _status_label(status: Any) -> str:
    text = str(status or "unknown").replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def _fmt_num(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def _fmt_values(reported: Any, computed: Any, unit: Any) -> str:
    unit = str(unit or "")
    suffix = ""
    tail = ""
    if unit.startswith("percent"):
        suffix = "%"
        if unit == "percent_relative":
            tail = " (relative change)"
        elif "point" in unit:
            tail = " (percentage points)"
    elif unit:
        suffix = f" {unit}"
    return f"Reported {_fmt_num(reported)}{suffix} vs computed {_fmt_num(computed)}{suffix}{tail}"


def _paths(items: Iterable[Any], limit: int = 3) -> str:
    seen: list[str] = []
    for item in items or ():
        path = _get(item, "path") if not isinstance(item, str) else item
        if path and path not in seen:
            seen.append(str(path))
    if not seen:
        return "(no evidence path)"
    shown = ", ".join(f"`{p}`" for p in seen[:limit])
    if len(seen) > limit:
        shown += f" +{len(seen) - limit} more"
    return shown


def _short(text: Any, limit: int = 140) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# --------------------------------------------------------------------------------------
# Message formatting (plan §16.5)
# --------------------------------------------------------------------------------------


def format_alert(event: Any, *, metadata_only: bool = False) -> str:
    """One message per status change.

    Metadata-only output matches the plan §16.5 example plus a "why" hint::

        ⚠️ *demo-001 · claim-003* changed from *Supported* to *Conflicting*
        Cause: `originals/results.csv` was updated at 2:02 PM.
        Values and explanation are in the ClaimTrace dashboard.
        Ask: @ClaimTrace why claim-003?
    """
    project_id = _get(event, "project_id", "?")
    claim_id = _get(event, "claim_id", "?")
    from_status = _get(event, "from_status")
    to_status = _get(event, "to_status")
    cause = _get(event, "cause") or {}
    path = _get(cause, "path") or _get(cause, "artifact_path")
    old_hash = _get(cause, "old_sha256")
    new_hash = _get(cause, "new_sha256")
    when = _fmt_clock(_get(event, "at"))

    emoji = _STATUS_EMOJI.get(str(to_status), "ℹ️")
    lines = [
        f"{emoji} *{project_id} · {claim_id}* changed from *{_status_label(from_status)}* "
        f"to *{_status_label(to_status)}*",
    ]
    if path:
        if old_hash and not new_hash:
            verb = "was removed"
        elif new_hash and not old_hash:
            verb = "was added"
        else:
            verb = "was updated"
        lines.append(f"Cause: `{path}` {verb} at {when}.")
    else:
        lines.append(f"Cause: re-check at {when}.")

    if metadata_only:
        lines.append(DASHBOARD_HINT)
    else:
        reported = _get(event, "reported_value")
        computed = _get(event, "computed_value")
        if reported is not None or computed is not None:
            lines.append(_fmt_values(reported, computed, _get(event, "unit")))
        claim_text = _get(event, "claim")
        if claim_text:
            lines.append(f'Claim: "{_short(claim_text, 200)}"')
        explanation = _get(event, "explanation")
        if explanation:
            lines.append(f"Why: {_short(explanation, 240)}")
    lines.append(f"Ask: @ClaimTrace why {claim_id}?")
    return "\n".join(lines)


def format_audit_result(
    run: Any,
    claims: Sequence[Any],
    *,
    dashboard_url: Optional[str] = None,
    metadata_only: bool = False,
) -> str:
    """Counts by status first, then only the claims that need attention."""
    project_id = _get(run, "project_id", "?")
    trigger = _get(run, "trigger") or "manual"
    claims = list(claims or ())
    checked = _get(run, "claims_checked")
    if checked is None:
        checked = len(claims)

    counts: dict[str, int] = {}
    for claim in claims:
        status = str(_get(claim, "status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    order = list(KNOWN_STATUSES) + sorted(s for s in counts if s not in KNOWN_STATUSES)
    counts_line = " · ".join(f"{_status_label(s)} {counts.get(s, 0)}" for s in order)

    header = f"🔎 *ClaimTrace audit · {project_id}* · {checked} claims checked"
    trigger_label = str(trigger).replace("_", " ")
    header += f" · trigger: {trigger_label}"
    lines = [header, counts_line]

    changed = list(_get(run, "changed_artifacts") or ())
    if changed:
        lines.append(f"Changed: {_paths(changed)}")

    attention = [c for c in claims if str(_get(c, "status") or "") not in OK_STATUSES]
    if attention:
        lines.append("Needs attention:")
        limit = 10
        for claim in attention[:limit]:
            claim_id = _get(claim, "claim_id", "?")
            status = _status_label(_get(claim, "status"))
            evidence = _paths(_get(claim, "evidence") or ())
            line = f"• {claim_id} · {status} · {evidence}"
            if not metadata_only:
                reported = _get(claim, "reported_value")
                computed = _get(claim, "computed_value")
                if reported is not None or computed is not None:
                    line += " · " + _fmt_values(reported, computed, _get(claim, "unit")).lower()
                explanation = _get(claim, "explanation")
                if explanation:
                    line += f" · {_short(explanation, 120)}"
            lines.append(line)
        if len(attention) > limit:
            lines.append(f"…and {len(attention) - limit} more")
    else:
        lines.append("All checked claims are supported.")

    errors = list(_get(run, "errors") or ())
    if errors:
        lines.append(f"Errors: {len(errors)} (see the dashboard)")

    if dashboard_url:
        lines.append(f"Details: {dashboard_url}")
    else:
        lines.append(DASHBOARD_HINT if metadata_only else "Full details in the ClaimTrace dashboard.")
    return "\n".join(lines)


def format_digest(digest: Any, *, metadata_only: bool = False) -> str:
    """A 5–10 line scheduled-sweep summary (plan §16.5 / §17.3 example)."""
    project_id = _get(digest, "project_id", "?")
    findings = list(_get(digest, "findings") or ())
    checked = _get(digest, "claims_checked")
    when = _fmt_clock(_get(digest, "created_at"))

    lines = [f"🕒 *ClaimTrace sweep · {project_id}* · {when}"]
    parts = []
    if checked is not None:
        parts.append(f"{checked} claims checked")
    parts.append(f"{len(findings)} finding{'s' if len(findings) != 1 else ''}")
    lines.append("• " + " · ".join(parts))

    if not metadata_only:
        summary = _get(digest, "summary_md")
        if summary:
            first = next(
                (ln.strip() for ln in str(summary).splitlines()
                 if ln.strip() and not ln.lstrip().startswith(("#", "```", "{"))),
                "",
            )
            if first:
                lines.append(f"• Summary: {_short(first, 200)}")

    max_findings = 6
    for finding in findings[:max_findings]:
        claim_id = _get(finding, "claim_id", "?")
        kind = str(_get(finding, "kind") or "finding").replace("_", " ")
        paths = _paths(_get(finding, "paths") or ())
        line = f"• {claim_id} ({kind}): {paths}"
        if not metadata_only:
            note = _get(finding, "note")
            if note:
                line += f" — {_short(note, 160)}"
        lines.append(line)
    if len(findings) > max_findings:
        lines.append(f"• …and {len(findings) - max_findings} more findings")
    if not findings:
        lines.append("• Nothing new since the last sweep.")

    lines.append(DASHBOARD_HINT if metadata_only else "Full digest in the ClaimTrace dashboard.")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Text hygiene
# --------------------------------------------------------------------------------------


def truncate_text(text: str, max_len: int = DEFAULT_MAX_LEN, note: str = TRUNCATION_NOTE) -> str:
    """Cut ``text`` to at most ``max_len`` characters, ending with ``note`` when cut.

    Prefers a line boundary so a bullet is not split mid-word.
    """
    if len(text) <= max_len:
        return text
    budget = max_len - len(note) - 1
    if budget <= 0:
        return note[:max_len]
    head = text[:budget]
    cut = head.rfind("\n")
    if cut >= budget // 2:
        head = head[:cut]
    return head.rstrip() + "\n" + note


def neutralize_media(text: str) -> str:
    """Break the ``MEDIA:<path>`` attachment trigger of ``hermes send`` inside text."""
    return _MEDIA_RE.sub(lambda m: m.group(0)[:-1] + "​:", text)


def redact_tokens(text: Any) -> str:
    """Replace Slack token-like strings (``xoxb-…``, ``xapp-…``) with a placeholder."""
    if text is None:
        return ""
    return _TOKEN_RE.sub("[REDACTED-TOKEN]", str(text))


def clean_cli_output(text: Any) -> str:
    """Strip ANSI colour codes and NemoClaw's ``Active gateway set to …`` line."""
    if not text:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    text = _ANSI_RE.sub("", str(text))
    text = _GATEWAY_LINE_RE.sub("", text)
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip()).strip()


def _parse_json_blob(text: str) -> Optional[Any]:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start : end + 1])
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------------------
# Command construction and binary resolution
# --------------------------------------------------------------------------------------


def slack_target(channel: str, thread_ts: Optional[str] = None) -> str:
    """``slack:C0123ABCD`` or ``slack:C0123ABCD:<thread_ts>`` (verified ``--to`` syntax)."""
    channel = str(channel).strip()
    if not channel:
        raise ValueError("target_channel must not be empty")
    target = channel if channel.startswith("slack:") else f"slack:{channel}"
    if thread_ts:
        target = f"{target}:{str(thread_ts).strip()}"
    return target


def build_send_argv(
    nemohermes_bin: str,
    sandbox: str,
    target: str,
    text: str,
    *,
    timeout_s: Optional[float] = None,
) -> list[str]:
    """Argument list for one send; see the module docstring for the verified syntax."""
    argv = [nemohermes_bin, sandbox, "exec"]
    if timeout_s:
        argv += ["--timeout", str(int(timeout_s))]
    argv += ["--", "hermes", "send", "--to", target, "--json", text]
    return argv


def build_list_argv(nemohermes_bin: str, sandbox: str, platform: str = "slack") -> list[str]:
    """Argument list for ``hermes send --list <platform> --json`` (read-only)."""
    return [nemohermes_bin, sandbox, "exec", "--", "hermes", "send", "--list", platform, "--json"]


def _version_key(path: str) -> tuple:
    match = re.search(r"/v(\d+)\.(\d+)\.(\d+)/", path)
    return tuple(int(x) for x in match.groups()) if match else (0, 0, 0)


def resolve_nemohermes_bin(
    *,
    env: Optional[Mapping[str, str]] = None,
    which: Callable[[str], Optional[str]] = shutil.which,
    home: Optional[os.PathLike | str] = None,
) -> Optional[str]:
    """Find ``nemohermes``: ``$NEMOHERMES_BIN``, then PATH, then the newest nvm Node bin."""
    env = os.environ if env is None else env
    configured = env.get("NEMOHERMES_BIN")
    if configured:
        return os.path.expanduser(configured)
    found = which("nemohermes")
    if found:
        return found
    base = Path(home) if home is not None else Path.home()
    candidates = sorted(glob.glob(str(base / _NVM_GLOB)), key=_version_key, reverse=True)
    for candidate in candidates:
        if os.access(candidate, os.X_OK):
            return candidate
    return None


# --------------------------------------------------------------------------------------
# Runner and notifier
# --------------------------------------------------------------------------------------


class Runner(Protocol):
    def __call__(self, argv: Sequence[str], timeout_s: float) -> Any:  # CompletedProcess-like
        ...


def subprocess_runner(argv: Sequence[str], timeout_s: float) -> subprocess.CompletedProcess:
    """Default runner: argument list, no shell, no inherited stdin, captured output."""
    return subprocess.run(  # noqa: S603 - argv list, never shell=True
        list(argv),
        capture_output=True,
        text=True,
        timeout=timeout_s,
        stdin=subprocess.DEVNULL,
        check=False,
    )


@dataclass
class SendResult:
    ok: bool
    status: str  # sent | failed | skipped
    kind: str
    target: str
    project_id: str
    event_ids: list[str]
    attempts: int = 0
    error: Optional[str] = None
    deduped: bool = False
    notification_id: Any = None
    sent_at: Optional[datetime] = None
    response: Any = None  # parsed --json output when available
    message_ts: Optional[str] = None


class SlackNotifier:
    """Send ClaimTrace messages to Slack via ``nemohermes <sandbox> exec -- hermes send``.

    ``runner(argv, timeout_s)`` must return an object with ``returncode``, ``stdout`` and
    ``stderr`` (``subprocess.CompletedProcess`` by default). ``notifications`` is a
    duck-typed collection with ``find_one``/``insert_one``/``update_one`` (a pymongo
    collection or the in-memory fake in the tests); ``None`` disables dedupe/recording.
    """

    def __init__(
        self,
        runner: Optional[Runner] = None,
        notifications: Any = None,
        sandbox: str = "my-hermes",
        nemohermes_bin: Optional[str] = None,
        timeout_s: float = 30,
        retries: int = 1,
        metadata_only: bool = False,
        *,
        max_len: int = DEFAULT_MAX_LEN,
        retry_delay_s: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.runner: Runner = runner or subprocess_runner
        self.notifications = notifications
        self.sandbox = sandbox
        self._bin = nemohermes_bin
        self.timeout_s = float(timeout_s)
        self.retries = max(0, int(retries))
        self.metadata_only = bool(metadata_only)
        self.max_len = int(max_len)
        self.retry_delay_s = float(retry_delay_s)
        self._sleep = sleep
        self._clock = clock

    # -- binary ------------------------------------------------------------------------

    @property
    def nemohermes_bin(self) -> Optional[str]:
        if self._bin is None:
            self._bin = resolve_nemohermes_bin()
            if self._bin:
                logger.info("nemohermes resolved to %s", self._bin)
        return self._bin

    # -- formatting shortcuts (honour the notifier's metadata_only setting) -------------

    def alert_text(self, event: Any) -> str:
        return format_alert(event, metadata_only=self.metadata_only)

    def audit_result_text(self, run: Any, claims: Sequence[Any], dashboard_url: Optional[str] = None) -> str:
        return format_audit_result(run, claims, dashboard_url=dashboard_url, metadata_only=self.metadata_only)

    def digest_text(self, digest: Any) -> str:
        return format_digest(digest, metadata_only=self.metadata_only)

    # -- notifications collection ------------------------------------------------------

    def _already_sent(self, project_id: str, kind: str, target: str, event_ids: list[str]) -> bool:
        if self.notifications is None or not event_ids:
            return False
        doc = self.notifications.find_one(
            {
                "project_id": project_id,
                "kind": kind,
                "target": target,
                "event_ids": event_ids,
                "status": "sent",
            }
        )
        return doc is not None

    def _record_start(self, doc: dict) -> Any:
        if self.notifications is None:
            return None
        inserted = self.notifications.insert_one(doc)
        return getattr(inserted, "inserted_id", doc.get("_id"))

    def _record_update(self, notification_id: Any, fields: dict) -> None:
        if self.notifications is None or notification_id is None:
            return
        self.notifications.update_one({"_id": notification_id}, {"$set": fields})

    # -- sending -----------------------------------------------------------------------

    def send(
        self,
        kind: str,
        target_channel: str,
        text: str,
        *,
        project_id: str,
        event_ids: Iterable[str],
        triggered_at: Any,
        thread_ts: Optional[str] = None,
    ) -> SendResult:
        """Send ``text`` to Slack once per ``(project_id, kind, target, event_ids)``."""
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
        target = slack_target(target_channel, thread_ts)
        ids = sorted({str(e) for e in (event_ids or ())})
        result = SendResult(ok=False, status="failed", kind=kind, target=target, project_id=project_id, event_ids=ids)

        if self._already_sent(project_id, kind, target, ids):
            logger.info("slack %s for %s to %s already sent (events=%d); skipping", kind, project_id, target, len(ids))
            result.status, result.deduped = "skipped", True
            return result

        text = neutralize_media(truncate_text(text, self.max_len))
        if not text.strip():
            result.error = "empty message text"
            return result

        bin_path = self.nemohermes_bin
        doc = {
            "project_id": project_id,
            "kind": kind,
            "target": target,
            "event_ids": ids,
            "triggered_at": triggered_at,
            "sent_at": None,
            "status": "pending",
            "error": None,
            "attempts": 0,
            "text_len": len(text),
            "metadata_only": self.metadata_only,
        }
        result.notification_id = self._record_start(doc)

        if not bin_path:
            result.error = "nemohermes binary not found (set NEMOHERMES_BIN)"
            self._record_update(result.notification_id, {"status": "failed", "error": result.error})
            logger.error("slack %s for %s: %s", kind, project_id, result.error)
            return result

        argv = build_send_argv(bin_path, self.sandbox, target, text, timeout_s=self.timeout_s)
        if not self.metadata_only:
            logger.debug("slack %s text preview: %s", kind, _short(text, 200))

        max_attempts = self.retries + 1
        for attempt in range(1, max_attempts + 1):
            result.attempts = attempt
            error, retryable = self._run_once(argv, result)
            if error is None:
                result.ok, result.status, result.error = True, "sent", None
                result.sent_at = self._clock()
                self._record_update(
                    result.notification_id,
                    {"status": "sent", "sent_at": result.sent_at, "error": None, "attempts": attempt,
                     "message_ts": result.message_ts},
                )
                logger.info(
                    "slack %s for %s sent to %s (events=%d, attempt %d/%d)",
                    kind, project_id, target, len(ids), attempt, max_attempts,
                )
                return result
            result.error = error
            self._record_update(result.notification_id, {"status": "failed", "error": error, "attempts": attempt})
            logger.warning(
                "slack %s for %s to %s failed (attempt %d/%d): %s",
                kind, project_id, target, attempt, max_attempts, error,
            )
            if not retryable or attempt >= max_attempts:
                break
            if self.retry_delay_s > 0:
                self._sleep(self.retry_delay_s * attempt)
        return result

    def _run_once(self, argv: Sequence[str], result: SendResult) -> tuple[Optional[str], bool]:
        """Run the command once. Returns ``(error, retryable)``; ``error`` is None on success."""
        # Give the outer process a little longer than the in-sandbox --timeout.
        try:
            proc = self.runner(argv, self.timeout_s + 15)
        except subprocess.TimeoutExpired:
            return f"timed out after {self.timeout_s:g}s", True
        except FileNotFoundError as exc:
            return redact_tokens(f"nemohermes not executable: {exc}"), False
        except OSError as exc:
            return redact_tokens(f"could not run nemohermes: {exc}"), False

        stdout = clean_cli_output(getattr(proc, "stdout", ""))
        stderr = clean_cli_output(getattr(proc, "stderr", ""))
        rc = getattr(proc, "returncode", 1)
        if rc == 0:
            result.response = _parse_json_blob(stdout)
            if isinstance(result.response, Mapping):
                ts = result.response.get("ts") or result.response.get("message_id") or result.response.get("id")
                result.message_ts = str(ts) if ts else None
            return None, False
        detail = redact_tokens(_short(stderr or stdout, 300)) or "no output"
        label = {1: "delivery/backend error", 2: "usage error"}.get(rc, f"exit code {rc}")
        return f"{label} (rc={rc}): {detail}", rc != 2

    # -- diagnostics -------------------------------------------------------------------

    def list_targets(self, platform: str = "slack") -> tuple[int, str]:
        """Run ``hermes send --list <platform> --json`` (read-only). Returns (rc, output)."""
        bin_path = self.nemohermes_bin
        if not bin_path:
            return 127, "nemohermes binary not found"
        argv = build_list_argv(bin_path, self.sandbox, platform)
        try:
            proc = self.runner(argv, self.timeout_s + 15)
        except subprocess.TimeoutExpired:
            return 124, f"timed out after {self.timeout_s:g}s"
        output = clean_cli_output(getattr(proc, "stdout", "")) or clean_cli_output(getattr(proc, "stderr", ""))
        return getattr(proc, "returncode", 1), redact_tokens(output)
