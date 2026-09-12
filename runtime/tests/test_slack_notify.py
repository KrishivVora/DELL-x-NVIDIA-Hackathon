"""Tests for worker/slack_notify.py (stdlib unittest only; nothing is sent anywhere).

Run from ``runtime/``::

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import logging
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker import slack_notify as sn  # noqa: E402
from worker.slack_notify import (  # noqa: E402
    AlertEvent,
    AuditRun,
    Claim,
    Digest,
    Finding,
    SlackNotifier,
    build_send_argv,
    clean_cli_output,
    format_alert,
    format_audit_result,
    format_digest,
    neutralize_media,
    redact_tokens,
    resolve_nemohermes_bin,
    slack_target,
    truncate_text,
)

BIN = "/fake/bin/nemohermes"
CHANNEL = "C0123ABCD"
TRIGGERED = datetime(2026, 9, 12, 14, 2, 10, tzinfo=timezone.utc)
# The module's logger has no handlers in tests; a NullHandler keeps warnings off stderr.
logging.getLogger("claimtrace.slack_notify").addHandler(logging.NullHandler())

GATEWAY_LINE = "\x1b[1m\x1b[32m✓\x1b[39m\x1b[0m Active gateway set to 'nemoclaw'\n"


# --------------------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------------------


class FakeRunner:
    """Records argv and replays scripted outcomes (CompletedProcess-like or exceptions)."""

    def __init__(self, outcomes=None):
        self.calls: list[tuple[list[str], float]] = []
        self.outcomes = list(outcomes or [])

    def __call__(self, argv, timeout_s):
        self.calls.append((list(argv), timeout_s))
        if not self.outcomes:
            return SimpleNamespace(returncode=0, stdout=GATEWAY_LINE + '{"ok": true, "ts": "1757700130.000100"}', stderr="")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def proc(rc=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)


class FakeCollection:
    """Minimal pymongo-like collection: find_one / insert_one / update_one with $set."""

    def __init__(self):
        self.docs: list[dict] = []
        self._next_id = 1

    @staticmethod
    def _matches(doc, flt):
        return all(doc.get(k) == v for k, v in flt.items())

    def find_one(self, flt):
        return next((d for d in self.docs if self._matches(d, flt)), None)

    def insert_one(self, doc):
        doc = dict(doc)
        doc.setdefault("_id", f"n{self._next_id}")
        self._next_id += 1
        self.docs.append(doc)
        return SimpleNamespace(inserted_id=doc["_id"])

    def update_one(self, flt, update):
        doc = self.find_one(flt)
        if doc is not None:
            doc.update(update.get("$set", {}))
        return SimpleNamespace(matched_count=int(doc is not None))


def make_notifier(runner=None, coll=None, **kw):
    kw.setdefault("nemohermes_bin", BIN)
    kw.setdefault("retry_delay_s", 0)
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("clock", lambda: datetime(2026, 9, 12, 14, 2, 12, tzinfo=timezone.utc))
    return SlackNotifier(runner=runner or FakeRunner(), notifications=coll, **kw)


def sample_alert():
    return AlertEvent(
        project_id="demo-001",
        claim_id="claim-003",
        from_status="supported",
        to_status="conflicting",
        cause={"path": "originals/results.csv", "old_sha256": "aaa", "new_sha256": "bbb"},
        at=datetime(2026, 9, 12, 14, 2, 10),
        run_id="run-0007",
        event_id="evt-1",
        reported_value=12.0,
        computed_value=10.8,
        unit="percent_relative",
        claim="Our method improves accuracy by 12%.",
        explanation="results.csv shows a 10.8% relative improvement on the test split, not 12%.",
    )


def sample_claims():
    return [
        Claim("claim-001", "supported", [{"path": "originals/table1.csv"}], 0.91, 0.91, "fraction"),
        Claim(
            "claim-003",
            "conflicting",
            [{"path": "originals/results.csv"}, {"path": "originals/evaluation.ipynb"}],
            12.0,
            10.8,
            "percent_relative",
            claim="Our method improves accuracy by 12%.",
            explanation="results.csv shows a 10.8% relative improvement, not 12%.",
        ),
        Claim("claim-005", "missing_evidence", []),
    ]


def sample_digest():
    return Digest(
        project_id="demo-001",
        hermes_job_id="job-1",
        created_at=datetime(2026, 9, 12, 15, 0),
        summary_md="# Sweep\nTwo items need a look this hour.\n```json\n{}\n```",
        findings=[
            Finding("claim-005", "new_evidence", ["originals/robustness.csv"], "Noise ablation results added."),
            {"claim_id": "claim-002", "kind": "stale_result",
             "paths": ["originals/results.csv", "originals/eval.py"], "note": "eval.py modified after results.csv."},
        ],
        claims_checked=6,
        digest_id="dig-1",
    )


# --------------------------------------------------------------------------------------
# argv construction
# --------------------------------------------------------------------------------------


class ArgvTests(unittest.TestCase):
    def test_argv_without_thread(self):
        runner = FakeRunner()
        n = make_notifier(runner, timeout_s=30)
        res = n.send("alert", CHANNEL, "hello\nworld", project_id="demo-001", event_ids=["evt-1"], triggered_at=TRIGGERED)
        self.assertTrue(res.ok)
        self.assertEqual(len(runner.calls), 1)
        argv, timeout = runner.calls[0]
        self.assertEqual(
            argv,
            [BIN, "my-hermes", "exec", "--timeout", "30", "--", "hermes", "send", "--to", "slack:C0123ABCD", "--json", "hello\nworld"],
        )
        self.assertEqual(timeout, 45)  # outer timeout = --timeout + grace
        self.assertEqual(res.message_ts, "1757700130.000100")

    def test_argv_with_thread(self):
        runner = FakeRunner()
        n = make_notifier(runner, sandbox="other-box", timeout_s=10)
        n.send("audit_result", CHANNEL, "result", project_id="p", event_ids=["e"], triggered_at=TRIGGERED, thread_ts="1757700000.000200")
        argv, _ = runner.calls[0]
        self.assertEqual(
            argv,
            [BIN, "other-box", "exec", "--timeout", "10", "--", "hermes", "send", "--to", "slack:C0123ABCD:1757700000.000200", "--json", "result"],
        )

    def test_build_send_argv_and_target_helpers(self):
        self.assertEqual(slack_target("slack:C1"), "slack:C1")
        self.assertEqual(slack_target("#claimtrace"), "slack:#claimtrace")
        self.assertEqual(slack_target("C1", "1.2"), "slack:C1:1.2")
        with self.assertRaises(ValueError):
            slack_target("")
        self.assertEqual(
            build_send_argv("nh", "sb", "slack:C1", "t"),
            ["nh", "sb", "exec", "--", "hermes", "send", "--to", "slack:C1", "--json", "t"],
        )

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            make_notifier().send("nope", CHANNEL, "x", project_id="p", event_ids=[], triggered_at=TRIGGERED)

    def test_media_prefix_neutralized_in_argv(self):
        runner = FakeRunner()
        make_notifier(runner).send("alert", CHANNEL, "see MEDIA:/etc/passwd", project_id="p", event_ids=["e"], triggered_at=TRIGGERED)
        text = runner.calls[0][0][-1]
        self.assertNotIn("MEDIA:", text)
        self.assertIn("MEDIA​:", text)
        self.assertEqual(neutralize_media("no media here"), "no media here")


# --------------------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------------------


class FormatAlertTests(unittest.TestCase):
    def test_metadata_only_matches_plan_example(self):
        text = format_alert(sample_alert(), metadata_only=True)
        self.assertEqual(
            text.splitlines(),
            [
                "⚠️ *demo-001 · claim-003* changed from *Supported* to *Conflicting*",
                "Cause: `originals/results.csv` was updated at 2:02 PM.",
                "Values and explanation are in the ClaimTrace dashboard.",
                "Ask: @ClaimTrace why claim-003?",
            ],
        )
        for leak in ("12", "10.8", "accuracy", "improves"):
            self.assertNotIn(leak, text)

    def test_full_mode_includes_values_and_claim(self):
        text = format_alert(sample_alert())
        self.assertIn("Reported 12% vs computed 10.8% (relative change)", text)
        self.assertIn('Claim: "Our method improves accuracy by 12%."', text)
        self.assertIn("Why: results.csv shows", text)
        self.assertIn("Ask: @ClaimTrace why claim-003?", text)
        self.assertNotIn("Values and explanation are in", text)

    def test_dict_input_and_removed_file(self):
        event = {
            "project_id": "demo-001", "claim_id": "claim-004", "from_status": "supported",
            "to_status": "missing_evidence", "cause": {"path": "originals/x.csv", "old_sha256": "a", "new_sha256": None},
            "at": "2026-09-12T09:05:00",
        }
        text = format_alert(event, metadata_only=True)
        self.assertIn("❓ *demo-001 · claim-004* changed from *Supported* to *Missing evidence*", text)
        self.assertIn("`originals/x.csv` was removed at 9:05 AM.", text)


class FormatAuditResultTests(unittest.TestCase):
    def setUp(self):
        self.run = AuditRun("demo-001", "file_change", ["originals/results.csv"], claims_checked=6, run_id="run-7")

    def test_metadata_only(self):
        text = format_audit_result(self.run, sample_claims(), dashboard_url="http://dash.local:8501", metadata_only=True)
        lines = text.splitlines()
        self.assertEqual(lines[0], "\U0001f50e *ClaimTrace audit · demo-001* · 6 claims checked · trigger: file change")
        self.assertEqual(lines[1], "Supported 1 · Conflicting 1 · Unsupported 0 · Missing evidence 1")
        self.assertIn("Needs attention:", lines)
        self.assertIn("• claim-003 · Conflicting · `originals/results.csv`, `originals/evaluation.ipynb`", lines)
        self.assertIn("• claim-005 · Missing evidence · (no evidence path)", lines)
        self.assertNotIn("claim-001", text)  # supported claims are not listed
        self.assertEqual(lines[-1], "Details: http://dash.local:8501")
        for leak in ("12", "10.8", "improves", "not 12"):
            self.assertNotIn(leak, text)

    def test_full_mode(self):
        text = format_audit_result(self.run, sample_claims())
        self.assertIn("reported 12% vs computed 10.8% (relative change)", text)
        self.assertIn("results.csv shows a 10.8% relative improvement", text)
        self.assertNotIn("claim-001", text)

    def test_all_supported_and_errors(self):
        run = AuditRun("demo-001", "slack", claims_checked=2, errors=["boom"])
        text = format_audit_result(run, [Claim("claim-001", "supported"), Claim("claim-002", "supported")], metadata_only=True)
        self.assertIn("All checked claims are supported.", text)
        self.assertIn("Errors: 1", text)
        self.assertNotIn("boom", text)


class FormatDigestTests(unittest.TestCase):
    def test_metadata_only(self):
        text = format_digest(sample_digest(), metadata_only=True)
        lines = text.splitlines()
        self.assertEqual(lines[0], "\U0001f552 *ClaimTrace sweep · demo-001* · 3:00 PM")
        self.assertEqual(lines[1], "• 6 claims checked · 2 findings")
        self.assertEqual(lines[2], "• claim-005 (new evidence): `originals/robustness.csv`")
        self.assertEqual(lines[3], "• claim-002 (stale result): `originals/results.csv`, `originals/eval.py`")
        self.assertNotIn("Noise ablation", text)
        self.assertNotIn("Two items", text)
        self.assertTrue(5 <= len(lines) <= 10, lines)

    def test_full_mode(self):
        text = format_digest(sample_digest())
        lines = text.splitlines()
        self.assertIn("• Summary: Two items need a look this hour.", lines)
        self.assertIn("• claim-005 (new evidence): `originals/robustness.csv` — Noise ablation results added.", lines)
        self.assertNotIn("Sweep\n", text)  # markdown heading skipped
        self.assertTrue(5 <= len(lines) <= 10, lines)

    def test_no_findings(self):
        text = format_digest(Digest("demo-001", [], datetime(2026, 9, 12, 16, 0)), metadata_only=True)
        self.assertIn("• 0 findings", text)
        self.assertIn("Nothing new since the last sweep.", text)


# --------------------------------------------------------------------------------------
# dedupe, recording, retries, timeouts
# --------------------------------------------------------------------------------------


class NotifierBehaviourTests(unittest.TestCase):
    def test_dedupe_skips_when_already_sent(self):
        coll = FakeCollection()
        runner = FakeRunner()
        n = make_notifier(runner, coll)
        first = n.send("alert", CHANNEL, "t", project_id="demo-001", event_ids=["evt-2", "evt-1"], triggered_at=TRIGGERED)
        self.assertTrue(first.ok)
        self.assertEqual(coll.docs[0]["event_ids"], ["evt-1", "evt-2"])  # stored sorted
        again = n.send("alert", CHANNEL, "t", project_id="demo-001", event_ids=["evt-1", "evt-2"], triggered_at=TRIGGERED)
        self.assertTrue(again.deduped)
        self.assertEqual(again.status, "skipped")
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(len(coll.docs), 1)
        # A different target, kind or event set is not a duplicate.
        n.send("alert", "C9", "t", project_id="demo-001", event_ids=["evt-1", "evt-2"], triggered_at=TRIGGERED)
        n.send("digest", CHANNEL, "t", project_id="demo-001", event_ids=["evt-1", "evt-2"], triggered_at=TRIGGERED)
        n.send("alert", CHANNEL, "t", project_id="demo-001", event_ids=["evt-3"], triggered_at=TRIGGERED)
        self.assertEqual(len(runner.calls), 4)

    def test_failed_notification_does_not_block_resend(self):
        coll = FakeCollection()
        n = make_notifier(FakeRunner([proc(1, stderr="slack down")]), coll, retries=0)
        first = n.send("alert", CHANNEL, "t", project_id="p", event_ids=["e"], triggered_at=TRIGGERED)
        self.assertFalse(first.ok)
        second = n.send("alert", CHANNEL, "t", project_id="p", event_ids=["e"], triggered_at=TRIGGERED)
        self.assertTrue(second.ok)
        self.assertFalse(second.deduped)

    def test_record_fields_on_success(self):
        coll = FakeCollection()
        n = make_notifier(FakeRunner(), coll, metadata_only=True)
        res = n.send("digest", CHANNEL, "some text", project_id="demo-001", event_ids=["dig-1"], triggered_at=TRIGGERED, thread_ts="1.5")
        doc = coll.docs[0]
        self.assertEqual(doc["_id"], res.notification_id)
        self.assertEqual(doc["project_id"], "demo-001")
        self.assertEqual(doc["kind"], "digest")
        self.assertEqual(doc["target"], "slack:C0123ABCD:1.5")
        self.assertEqual(doc["triggered_at"], TRIGGERED)
        self.assertEqual(doc["sent_at"], datetime(2026, 9, 12, 14, 2, 12, tzinfo=timezone.utc))
        self.assertEqual(doc["status"], "sent")
        self.assertIsNone(doc["error"])
        self.assertEqual(doc["attempts"], 1)
        self.assertNotIn("text", doc)  # message text is never stored
        self.assertEqual(doc["text_len"], len("some text"))

    def test_failure_recorded_then_retry_succeeds(self):
        coll = FakeCollection()
        runner = FakeRunner([proc(1, stderr=GATEWAY_LINE + "Error: delivery failed"), proc(0, stdout='{"ok": true}')])
        n = make_notifier(runner, coll, retries=1)
        res = n.send("alert", CHANNEL, "t", project_id="p", event_ids=["e"], triggered_at=TRIGGERED)
        self.assertTrue(res.ok)
        self.assertEqual(res.attempts, 2)
        self.assertIsNone(res.error)
        self.assertEqual(len(runner.calls), 2)
        doc = coll.docs[0]
        self.assertEqual(doc["status"], "sent")
        self.assertEqual(doc["attempts"], 2)
        self.assertIsNone(doc["error"])

    def test_all_attempts_fail_records_failed(self):
        coll = FakeCollection()
        runner = FakeRunner([proc(1, stderr="a"), proc(1, stderr="b")])
        res = make_notifier(runner, coll, retries=1).send("alert", CHANNEL, "t", project_id="p", event_ids=["e"], triggered_at=TRIGGERED)
        self.assertFalse(res.ok)
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.attempts, 2)
        self.assertIn("delivery/backend error (rc=1): b", res.error)
        self.assertEqual(coll.docs[0]["status"], "failed")
        self.assertEqual(coll.docs[0]["error"], res.error)
        self.assertIsNone(coll.docs[0]["sent_at"])

    def test_usage_error_is_not_retried(self):
        runner = FakeRunner([proc(2, stderr="usage: hermes send")])
        res = make_notifier(runner, retries=3).send("alert", CHANNEL, "t", project_id="p", event_ids=["e"], triggered_at=TRIGGERED)
        self.assertFalse(res.ok)
        self.assertEqual(len(runner.calls), 1)
        self.assertIn("usage error (rc=2)", res.error)

    def test_timeout_is_recorded_and_retried(self):
        coll = FakeCollection()
        runner = FakeRunner([subprocess.TimeoutExpired(cmd="nemohermes", timeout=45), proc(0)])
        res = make_notifier(runner, coll, timeout_s=30, retries=1).send("alert", CHANNEL, "t", project_id="p", event_ids=["e"], triggered_at=TRIGGERED)
        self.assertTrue(res.ok)
        self.assertEqual(res.attempts, 2)
        runner2 = FakeRunner([subprocess.TimeoutExpired(cmd="nemohermes", timeout=45)])
        res2 = make_notifier(runner2, retries=0, timeout_s=30).send("alert", CHANNEL, "t", project_id="p", event_ids=["e"], triggered_at=TRIGGERED)
        self.assertFalse(res2.ok)
        self.assertEqual(res2.error, "timed out after 30s")

    def test_missing_binary_recorded_without_retry(self):
        runner = FakeRunner([FileNotFoundError("nemohermes")])
        res = make_notifier(runner, FakeCollection(), retries=2).send("alert", CHANNEL, "t", project_id="p", event_ids=["e"], triggered_at=TRIGGERED)
        self.assertFalse(res.ok)
        self.assertEqual(len(runner.calls), 1)
        self.assertIn("not executable", res.error)

    def test_no_collection_still_sends(self):
        runner = FakeRunner()
        res = make_notifier(runner, None).send("alert", CHANNEL, "t", project_id="p", event_ids=["e"], triggered_at=TRIGGERED)
        self.assertTrue(res.ok)
        self.assertIsNone(res.notification_id)

    def test_metadata_only_never_logs_text(self):
        runner = FakeRunner([proc(1, stderr="SECRET-CLAIM-TEXT in stderr"), proc(0)])
        n = make_notifier(runner, metadata_only=True, retries=1)
        with self.assertLogs("claimtrace.slack_notify", level="DEBUG") as cm:
            n.send("alert", CHANNEL, "SECRET-CLAIM-TEXT body", project_id="p", event_ids=["e"], triggered_at=TRIGGERED)
        joined = "\n".join(cm.output)
        self.assertNotIn("SECRET-CLAIM-TEXT body", joined)


# --------------------------------------------------------------------------------------
# truncation, redaction, output cleaning
# --------------------------------------------------------------------------------------


class TextHygieneTests(unittest.TestCase):
    def test_truncate_text(self):
        long = "\n".join(f"• line {i} " + "x" * 50 for i in range(200))
        out = truncate_text(long, max_len=500)
        self.assertLessEqual(len(out), 500)
        self.assertTrue(out.endswith(sn.TRUNCATION_NOTE))
        self.assertTrue(out.startswith("• line 0"))
        self.assertEqual(truncate_text("short", 500), "short")

    def test_send_truncates(self):
        runner = FakeRunner()
        n = make_notifier(runner, max_len=300)
        n.send("digest", CHANNEL, "y" * 1000, project_id="p", event_ids=["e"], triggered_at=TRIGGERED)
        sent = runner.calls[0][0][-1]
        self.assertLessEqual(len(sent), 300)
        self.assertIn("truncated", sent)

    def test_redact_tokens(self):
        # Fake tokens are assembled at runtime so secret scanners never see a literal.
        raw = "auth failed for " + "xox" + "b-1234567890-abcDEF and " + "xa" + "pp-1-A1B2-secret plus " + "xox" + "p-99-zz; ok"
        out = redact_tokens(raw)
        self.assertNotIn("xoxb-", out)
        self.assertNotIn("xapp-", out)
        self.assertNotIn("xoxp-", out)
        self.assertEqual(out.count("[REDACTED-TOKEN]"), 3)
        self.assertEqual(redact_tokens(None), "")

    def test_error_from_runner_is_redacted(self):
        coll = FakeCollection()
        runner = FakeRunner([proc(1, stderr="Slack API rejected token " + "xox" + "b-111-222-abc")])
        res = make_notifier(runner, coll, retries=0).send("alert", CHANNEL, "t", project_id="p", event_ids=["e"], triggered_at=TRIGGERED)
        self.assertNotIn("xoxb-", res.error)
        self.assertNotIn("xoxb-", coll.docs[0]["error"])
        self.assertIn("[REDACTED-TOKEN]", res.error)

    def test_clean_cli_output(self):
        raw = GATEWAY_LINE + "\x1b[31mError:\x1b[0m something\n\n"
        self.assertEqual(clean_cli_output(raw), "Error: something")
        self.assertEqual(clean_cli_output(GATEWAY_LINE), "")
        self.assertEqual(clean_cli_output(b"\x1b[32mok\x1b[0m"), "ok")
        self.assertEqual(clean_cli_output(None), "")

    def test_json_response_parsed_after_gateway_line(self):
        runner = FakeRunner([proc(0, stdout=GATEWAY_LINE + '{"ok": true, "ts": "1.23"}')])
        res = make_notifier(runner).send("alert", CHANNEL, "t", project_id="p", event_ids=["e"], triggered_at=TRIGGERED)
        self.assertEqual(res.response, {"ok": True, "ts": "1.23"})
        self.assertEqual(res.message_ts, "1.23")


# --------------------------------------------------------------------------------------
# binary resolution
# --------------------------------------------------------------------------------------


class ResolveBinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        for ver in ("v20.1.0", "v22.23.2", "v22.9.9"):
            d = self.home / ".nvm" / "versions" / "node" / ver / "bin"
            d.mkdir(parents=True)
            f = d / "nemohermes"
            f.write_text("#!/bin/sh\n")
            f.chmod(f.stat().st_mode | stat.S_IXUSR)

    def tearDown(self):
        self.tmp.cleanup()

    def test_env_var_wins(self):
        out = resolve_nemohermes_bin(env={"NEMOHERMES_BIN": "/opt/x/nemohermes"}, which=lambda _: "/usr/bin/nemohermes", home=self.home)
        self.assertEqual(out, "/opt/x/nemohermes")

    def test_path_second(self):
        out = resolve_nemohermes_bin(env={}, which=lambda _: "/usr/bin/nemohermes", home=self.home)
        self.assertEqual(out, "/usr/bin/nemohermes")

    def test_nvm_glob_picks_newest_version(self):
        out = resolve_nemohermes_bin(env={}, which=lambda _: None, home=self.home)
        self.assertEqual(out, str(self.home / ".nvm/versions/node/v22.23.2/bin/nemohermes"))

    def test_nvm_glob_skips_non_executable(self):
        newest = self.home / ".nvm/versions/node/v22.23.2/bin/nemohermes"
        newest.chmod(stat.S_IRUSR | stat.S_IWUSR)
        out = resolve_nemohermes_bin(env={}, which=lambda _: None, home=self.home)
        self.assertEqual(out, str(self.home / ".nvm/versions/node/v22.9.9/bin/nemohermes"))

    def test_nothing_found(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        self.assertIsNone(resolve_nemohermes_bin(env={}, which=lambda _: None, home=empty))

    def test_notifier_reports_missing_binary(self):
        coll = FakeCollection()
        n = SlackNotifier(runner=FakeRunner(), notifications=coll, nemohermes_bin=None, retry_delay_s=0)
        n._bin = None
        with mock.patch.object(sn, "resolve_nemohermes_bin", return_value=None):
            res = n.send("alert", CHANNEL, "t", project_id="p", event_ids=["e"], triggered_at=TRIGGERED)
        self.assertFalse(res.ok)
        self.assertIn("nemohermes binary not found", res.error)
        self.assertEqual(coll.docs[0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
