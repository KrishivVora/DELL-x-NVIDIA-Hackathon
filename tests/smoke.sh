#!/usr/bin/env bash
# End-to-end rehearsal of the Labmate agent layer with no model involved.
# Proves: full audit -> supported/conflict/missing, file change detected,
# affected claim re-verified, status flips supported -> conflicting.
#
#     ./tests/smoke.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export LABMATE_PROJECTS_ROOT=tests
CT="python3 -m labmate --project fixture-project"
P=tests/fixture-project
# Agent-written files live under LABMATE_STATE_ROOT when it is set (read-only
# project mounts in the sandbox); otherwise in the project directory.
S="${LABMATE_STATE_ROOT:+$LABMATE_STATE_ROOT/fixture-project}"; S="${S:-$P}"

pass() { printf '  \033[32mok\033[0m  %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; exit 1; }
status_of() { $CT claim-list | python3 -c "import json,sys;print(next(c['status'] for c in json.load(sys.stdin)['claims'] if c['claim_id']=='$1'))"; }

echo "== reset =="
rm -rf "$S/audit-state.json" "$S/reports" "$S/meetings.json"
git checkout -- "$P/originals/results.csv" 2>/dev/null || true

echo "== full audit =="
$CT claim-set --claim "Our method improves accuracy by 12% over the baseline." \
  --location "Results, page 7" --type quantitative --reported 12.0 >/dev/null
$CT verify --op csv_delta --claim-id claim-001 --reported 12.0 \
  --args '{"path":"originals/results.csv","value_column":"accuracy","group_column":"method","baseline":"baseline","treatment":"ours"}' >/dev/null
[ "$(status_of claim-001)" = supported ] && pass "quantitative claim verified as supported" || fail "claim-001 should be supported"

$CT claim-set --claim "We evaluate our approach on five datasets." \
  --location "Methods, page 4" --type methodological --reported 5 >/dev/null
$CT verify --op csv_unique_count --claim-id claim-002 --reported 5 \
  --args '{"path":"originals/results.csv","column":"dataset"}' >/dev/null
[ "$(status_of claim-002)" = supported ] && pass "methodological claim verified as supported" || fail "claim-002 should be supported"

$CT claim-set --claim "The approach is more robust to input noise." \
  --location "Discussion, page 9" --type interpretive --status missing_evidence \
  --explanation "Every row of results.csv records noise_level 0.0, so no artifact varies noise." \
  --action "Add a noise sweep or soften the claim." --confidence high >/dev/null
[ "$(status_of claim-003)" = missing_evidence ] && pass "interpretive claim recorded as missing evidence" || fail "claim-003 should be missing_evidence"

$CT report >/dev/null
$CT snapshot >/dev/null
grep -q "Conflict" "$S/reports/latest.md" && fail "no conflict expected yet" || pass "report written with no conflicts"

echo "== no spurious change =="
[ "$($CT changes | python3 -c 'import json,sys;print(json.load(sys.stdin)["changed_count"])')" = 0 ] \
  && pass "no changes detected after snapshot" || fail "snapshot did not settle"

echo "== evidence file changes =="
cp tests/results-v2.csv "$P/originals/results.csv"
CHANGES=$($CT changes)
echo "$CHANGES" | grep -q '"originals/results.csv"' && pass "changed file detected" || fail "change not detected"
echo "$CHANGES" | python3 -c "
import json,sys
d=json.load(sys.stdin); ids=[c['claim_id'] for c in d['affected_claims']]
assert 'claim-001' in ids and 'claim-002' in ids, ids
assert 'claim-003' not in ids, 'claim-003 cites no file and must not be affected'
" && pass "affected claims mapped, unaffected claim excluded" || fail "affected-claim mapping wrong"

echo "== watcher fires =="
WATCH_OUT=$(python3 -m labmate.watcher --project fixture-project --once --dry-run)
case "$WATCH_OUT" in *"hermes -z"*) pass "watcher built a Hermes one-shot trigger";; *) fail "watcher did not trigger";; esac

echo "== incremental re-audit =="
git checkout -- "$P/originals/results.csv" 2>/dev/null || true
cp tests/results-v2.csv "$P/originals/results.csv"
$CT verify --op csv_delta --claim-id claim-001 --reported 12.0 \
  --args '{"path":"originals/results.csv","value_column":"accuracy","group_column":"method","baseline":"baseline","treatment":"ours"}' >/dev/null
[ "$(status_of claim-001)" = conflicting ] && pass "claim-001 flipped supported -> conflicting" || fail "claim-001 should now conflict"
$CT verify --op csv_unique_count --claim-id claim-002 --reported 5 \
  --args '{"path":"originals/results.csv","column":"dataset"}' >/dev/null
[ "$(status_of claim-002)" = supported ] && pass "claim-002 still supported" || fail "claim-002 should stay supported"

$CT report >/dev/null
grep -q "Conflict" "$S/reports/latest.md" && pass "report now shows the conflict" || fail "report missing conflict"


echo "== ask =="
ASK=$($CT ask --query "how much did accuracy improve" -k 3)
echo "$ASK" | python3 -c "
import json,sys
d=json.load(sys.stdin)
assert d['passages'], 'ask returned no passages'
assert d['passages'][0]['page'] == 7, d['passages'][0]
assert 'Cite every statement' in d['instruction']
" && pass "ask returns cited passages with page numbers" || fail "ask returned nothing usable"

echo "== meetings =="
$CT meeting-set --title "Weekly sync" --when "2026-09-15T10:00" --attendees "Dr. Rao" \
  --topics "accuracy improvements" "dataset coverage" >/dev/null
DIGEST=$($CT digest)
echo "$DIGEST" | python3 -c "
import json,sys
d=json.load(sys.stdin)
assert d['upcoming_meetings'], 'meeting not in digest'
assert any(i['status']=='conflicting' for i in d['open_items']), 'conflict not surfaced as an open item'
" && pass "digest surfaces the meeting and the open conflict" || fail "digest incomplete"

CTX=$($CT meeting-brief --id m-001)
echo "$CTX" | python3 -c "
import json,sys
d=json.load(sys.stdin)
topics=[b['topic'] for b in d['evidence_by_topic']]
assert topics == ['accuracy improvements','dataset coverage'], topics
assert d['open_items'], 'brief context missing open items'
assert 'next_step' in d
" && pass "brief context assembles passages, activity and open items" || fail "brief context incomplete"

$CT meeting-brief --id m-001 --summary "The reported 12% no longer matches the data; recomputed 7.4%." >/dev/null
BRIEF=$(ls "$P"/reports/brief-m-001-*.md | head -1)
grep -q "Open items from earlier analysis" "$BRIEF" && pass "brief written with sources and open items" || fail "brief missing sections"
grep -q "no longer matches" "$BRIEF" && pass "brief carries the written summary" || fail "summary not saved"

echo "== notification is sanitized =="
MSG=$($CT notify --reason "source file changed" | python3 -c 'import json,sys;print(json.load(sys.stdin)["message"])')
echo "$MSG"
for leak in "accuracy" "12" "78" "noise"; do
  case "$MSG" in *"$leak"*) fail "notification leaked '$leak'";; esac
done
pass "notification contains no research content"

echo "== error handling =="
ERR_OUT=$($CT verify --op csv_stat --args '{"path":"originals/results.csv","column":"nope"}' || true)
case "$ERR_OUT" in *"not in"*) pass "bad column error lists the real columns";; *) fail "error message unhelpful: $ERR_OUT";; esac
MISSING=$($CT verify --op csv_stat --args '{"path":"originals/nope.csv","column":"accuracy"}' || true)
case "$MISSING" in *"does not exist"*) pass "missing file returns a clear error";; *) fail "missing-file error unhelpful: $MISSING";; esac

git checkout -- "$P/originals/results.csv" 2>/dev/null || true
printf '\n\033[32mall checks passed\033[0m\n'
