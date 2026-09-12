#!/usr/bin/env bash
# verify-slack.sh: check that Slack is wired to the NemoClaw-managed Hermes sandbox.
#
# Read-only by default. Safe to run any time, including before Slack is configured
# (missing Slack is reported as WARN, not a crash).
#
# Usage:
#   runtime/scripts/verify-slack.sh
#   runtime/scripts/verify-slack.sh --post C0123ABCD   # also sends ONE test message
#   CLAIMTRACE_SANDBOX=my-hermes runtime/scripts/verify-slack.sh
#
# Exit status: 0 if nothing FAILed (WARN allowed), 1 if any check FAILed, 2 on usage error.
#
# What each check relies on (verified 2026-09-12 on the GB10: NemoClaw v0.0.123,
# OpenShell 0.0.106, Hermes Agent v0.20.6 in the sandbox). NemoClaw paths are relative to
# gb10-offline-bundle/NemoClaw/.
#   - `nemohermes <name> status [--json]`: text has "Phase: Ready" and, when mounts exist,
#     "Host mounts: <src> -> <target> (read-only)" (src/lib/actions/sandbox/status-text.ts);
#     JSON has .phase and .hostMounts[].
#   - `nemohermes <name> channels status --channel slack --json` (flags confirmed via --help)
#     returns {signals:[{label,severity,detail}]}. "Channel registration" is ok/"slack registered",
#     info/"slack not registered", or warn/"slack registered but currently paused"; "Policy
#     coverage" is ok when the slack preset is applied (src/lib/actions/sandbox/channel-status.ts:311-327).
#     `--wait` readiness is OpenClaw-only for Slack (src/lib/messaging/channels/slack/manifest.ts:222-234),
#     so this script does not use it.
#   - `nemohermes <name> policy list` marks applied presets with "●" and others with "○".
#   - `hermes send --help` in 0.20.6 lists "-l, --list". With Slack unconfigured,
#     `hermes send --list slack` exits 1 with "no targets found for platform 'slack'".
#   - `openshell forward list` prints "No active forwards." on this box because NemoClaw serves
#     8642 and 18789 with `openshell forward service <sandbox> --target-port <p> --local 127.0.0.1:<p>`
#     processes, which that list does not show. The check falls back to pgrep + ss.
#   - nemohermes prints "✓ Active gateway set to 'nemoclaw'" on stdout first; it is filtered out.
#
# Never prints Slack tokens: output is filtered for xoxb-/xapp- strings and #token= URLs.

set -uo pipefail
set +x

SANDBOX="${CLAIMTRACE_SANDBOX:-my-hermes}"
NODE_BIN="$HOME/.nvm/versions/node/v22.23.2/bin"
CMD_TIMEOUT="${CLAIMTRACE_VERIFY_TIMEOUT:-120}"
FORWARD_PORTS=(8642 18789)
export PATH="$NODE_BIN:$PATH"

POST_CHANNEL=""

usage() {
  cat <<EOF
Usage: $(basename "$0") [--post <channel_id>] [-h|--help]

Read-only checks that Slack is connected to sandbox '$SANDBOX'.

  --post <channel_id>  Also send one test message ("ClaimTrace is online") with
                       hermes send --to slack:<channel_id>. Only runs when passed.
  -h, --help           Show this help.

Environment:
  CLAIMTRACE_SANDBOX         Sandbox name (default: my-hermes)
  CLAIMTRACE_VERIFY_TIMEOUT  Per-command timeout in seconds (default: 120)
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --post)
      if [ $# -lt 2 ]; then
        usage >&2
        exit 2
      fi
      POST_CHANNEL="$2"
      shift 2
      ;;
    --post=*)
      POST_CHANNEL="${1#--post=}"
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -n "$POST_CHANNEL" ] && ! [[ "$POST_CHANNEL" =~ ^[CG][A-Z0-9]{2,}$ ]]; then
  printf -- '--post needs a Slack channel ID such as C0123ABCD (got an invalid value).\n' >&2
  exit 2
fi

if [ -t 1 ]; then
  C_G=$'\033[32m' C_Y=$'\033[33m' C_R=$'\033[31m' C_B=$'\033[1m' C_0=$'\033[0m'
else
  C_G="" C_Y="" C_R="" C_B="" C_0=""
fi

PASS_N=0
WARN_N=0
FAIL_N=0
pass() {
  PASS_N=$((PASS_N + 1))
  printf '  %sPASS%s  %s\n' "$C_G" "$C_0" "$*"
}
warn() {
  WARN_N=$((WARN_N + 1))
  printf '  %sWARN%s  %s\n' "$C_Y" "$C_0" "$*"
}
fail() {
  FAIL_N=$((FAIL_N + 1))
  printf '  %sFAIL%s  %s\n' "$C_R" "$C_0" "$*"
}
info() { printf '        %s\n' "$*"; }
indent() { sed 's/^[[:space:]]*/        /'; }
section() { printf '\n%s%s%s\n' "$C_B" "$*" "$C_0"; }

# Strip ANSI colors and the "Active gateway set" banner.
clean() { sed -E 's/\x1b\[[0-9;]*[A-Za-z]//g' | grep -v "Active gateway set to" || true; }
# Never show Slack tokens or dashboard tokens.
redact() { sed -E 's/(xox[abprs]|xapp)-[A-Za-z0-9_-]+/\1-<redacted>/g; s/#token=[^[:space:]"]+/#token=<redacted>/g'; }

# Run nemohermes non-interactively; stdout+stderr cleaned; returns nemohermes's exit code.
nh() {
  timeout "$CMD_TIMEOUT" nemohermes "$@" </dev/null 2>&1 | clean | redact
  return "${PIPESTATUS[0]}"
}

# Read mixed output on stdin; print the first JSON object found, compactly.
json_first_object() {
  python3 -c '
import json, sys
raw = sys.stdin.read()
i = raw.find("{")
if i < 0:
    sys.exit(3)
try:
    obj, _ = json.JSONDecoder().raw_decode(raw[i:])
except ValueError:
    sys.exit(3)
json.dump(obj, sys.stdout)
'
}

SLACK_REGISTERED=0

# ---------------------------------------------------------------------------
section "0. Prerequisites"
if ! command -v nemohermes >/dev/null 2>&1; then
  fail "nemohermes not found (expected in $NODE_BIN)"
  printf '\nResult: FAIL (cannot run further checks)\n'
  exit 1
fi
pass "nemohermes $(nemohermes --version 2>/dev/null | clean | grep -Eo 'v[0-9.]+' | head -n 1)"
for tool in python3 timeout curl; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    fail "$tool is required"
    printf '\nResult: FAIL (cannot run further checks)\n'
    exit 1
  fi
done

# ---------------------------------------------------------------------------
section "1. Sandbox status (nemohermes $SANDBOX status)"
status_txt="$(nh "$SANDBOX" status)"
rc=$?
if [ "$rc" -ne 0 ]; then
  fail "status exited $rc"
  printf '%s\n' "$status_txt" | tail -n 5 | indent
else
  phase="$(printf '%s\n' "$status_txt" | awk '$1 == "Phase:" { print $2; exit }')"
  if [ "$phase" = "Ready" ]; then
    pass "sandbox '$SANDBOX' phase Ready"
  else
    fail "sandbox '$SANDBOX' phase is '${phase:-unknown}' (expected Ready)"
  fi
  printf '%s\n' "$status_txt" |
    grep -Ei '^[[:space:]]*(Model|Provider|Inference|Policies|Agent|Host mounts):|slack| -> .*read-only' |
    head -n 14 | indent
fi

RECORDED_MOUNT=0
status_json="$(nh "$SANDBOX" status --json | json_first_object)"
if [ -n "$status_json" ]; then
  mounts="$(printf '%s' "$status_json" | python3 -c '
import json, sys
d = json.load(sys.stdin)
for m in d.get("hostMounts") or []:
    print("%s -> %s" % (m.get("source"), m.get("target")))
')"
  if printf '%s\n' "$mounts" | grep -q ' -> /sandbox/projects$'; then
    RECORDED_MOUNT=1
    pass "host mount recorded: $(printf '%s\n' "$mounts" | grep ' -> /sandbox/projects$' | head -n 1) (read-only)"
  else
    warn "no /sandbox/projects host mount recorded (plan §12.3 option A not applied yet)"
  fi
else
  warn "could not parse 'status --json'; skipped the host-mount record check"
fi

# ---------------------------------------------------------------------------
section "2. Slack channel status (nemohermes $SANDBOX channels status)"
ch_help="$(nh "$SANDBOX" channels status --help)"
if printf '%s\n' "$ch_help" | grep -q -- '--channel' && printf '%s\n' "$ch_help" | grep -q -- '--json'; then
  ch_json="$(nh "$SANDBOX" channels status --channel slack --json | json_first_object)"
  if [ -z "$ch_json" ]; then
    fail "could not parse 'channels status --channel slack --json'"
  else
    parsed="$(printf '%s' "$ch_json" | python3 -c '
import json, sys
d = json.load(sys.stdin)
for s in d.get("signals") or []:
    print("\t".join([str(s.get("label", "")), str(s.get("severity", "")), str(s.get("detail", ""))]))
')"
    reg_sev="$(printf '%s\n' "$parsed" | awk -F'\t' '$1 == "Channel registration" { print $2; exit }')"
    reg_detail="$(printf '%s\n' "$parsed" | awk -F'\t' '$1 == "Channel registration" { print $3; exit }')"
    case "$reg_sev" in
      ok)
        SLACK_REGISTERED=1
        pass "channel registration: $reg_detail"
        ;;
      info) warn "Slack not configured yet ($reg_detail). Run runtime/scripts/connect-slack.sh in your own terminal." ;;
      warn) warn "channel registration: $reg_detail (resume it with channels start slack)" ;;
      "") fail "no 'Channel registration' signal in channels status output" ;;
      *) fail "channel registration: $reg_detail ($reg_sev)" ;;
    esac

    pol_sev="$(printf '%s\n' "$parsed" | awk -F'\t' '$1 == "Policy coverage" { print $2; exit }')"
    pol_detail="$(printf '%s\n' "$parsed" | awk -F'\t' '$1 == "Policy coverage" { print $3; exit }')"
    if [ "$pol_sev" = "ok" ]; then
      pass "policy coverage: $pol_detail"
    elif [ "$SLACK_REGISTERED" = 1 ]; then
      fail "policy coverage: ${pol_detail:-unknown} (Slack is registered but its preset is not applied)"
    else
      warn "policy coverage: ${pol_detail:-unknown}"
    fi

    # Any other warn/fail signals (config comparisons, runtime health).
    while IFS=$'\t' read -r label sev detail; do
      [ -n "$label" ] || continue
      case "$label" in "Channel registration" | "Policy coverage") continue ;; esac
      case "$sev" in
        fail | error) fail "$label: $detail" ;;
        warn) warn "$label: $detail" ;;
        *) info "$label: $detail" ;;
      esac
    done <<<"$parsed"
  fi
else
  warn "'channels status' lacks --channel/--json here; falling back to text output"
  ch_txt="$(nh "$SANDBOX" channels status)"
  printf '%s\n' "$ch_txt" | indent
  if printf '%s\n' "$ch_txt" | grep -qi 'slack'; then
    SLACK_REGISTERED=1
    pass "slack appears in channels status"
  else
    warn "Slack not configured yet (not listed in channels status)"
  fi
fi

# ---------------------------------------------------------------------------
section "3. Network policy (nemohermes $SANDBOX policy list)"
pol_txt="$(nh "$SANDBOX" policy list)"
rc=$?
if [ "$rc" -ne 0 ]; then
  fail "policy list exited $rc"
else
  slack_line="$(printf '%s\n' "$pol_txt" | grep -E '^[[:space:]]*[●○][[:space:]]+slack([[:space:]]|$)' | head -n 1)"
  case "$slack_line" in
    *●*) pass "slack preset applied" ;;
    *○*)
      if [ "$SLACK_REGISTERED" = 1 ]; then
        fail "slack preset NOT applied although Slack is registered"
      else
        warn "slack preset not applied (expected until Slack is configured)"
      fi
      ;;
    *) fail "slack preset missing from policy list" ;;
  esac
  applied="$(printf '%s\n' "$pol_txt" | awk '$1 == "●" { printf "%s%s", sep, $2; sep = ", " }')"
  info "applied presets: ${applied:-none}"
  extra="$(printf '%s\n' "$pol_txt" | awk '$1 == "●" && $2 != "local-inference" && $2 != "slack" && $2 != "claimtrace-worker" { printf "%s%s", sep, $2; sep = ", " }')"
  [ -z "$extra" ] || info "not needed by ClaimTrace (plan §19, remove before the demo): $extra"
fi

# ---------------------------------------------------------------------------
section "4. Slack targets (hermes send --list slack, inside the sandbox)"
send_help="$(nh "$SANDBOX" exec -- hermes send --help)"
if ! printf '%s\n' "$send_help" | grep -qE -- '(^|[[:space:]])--list([[:space:],]|$)'; then
  fail "'hermes send --list' is not available in this Hermes build"
else
  list_out="$(nh "$SANDBOX" exec -- hermes send --list slack)"
  rc=$?
  if printf '%s\n' "$list_out" | grep -qi "no targets found for platform 'slack'"; then
    if [ "$SLACK_REGISTERED" = 1 ]; then
      fail "hermes send finds no Slack targets although Slack is registered (credentials not resolved?)"
    else
      warn "hermes send has no Slack targets yet (Slack not configured)"
    fi
  elif [ "$rc" -eq 0 ]; then
    pass "hermes send can see Slack"
    printf '%s\n' "$list_out" | head -n 10 | indent
  else
    fail "hermes send --list slack exited $rc"
    printf '%s\n' "$list_out" | tail -n 5 | indent
  fi
fi

# ---------------------------------------------------------------------------
section "5. Project mount inside the sandbox (/proc/mounts)"
mnt_out="$(nh "$SANDBOX" exec -- awk '$2 == "/sandbox/projects" { print $4 }' /proc/mounts)"
rc=$?
mnt_opts="$(printf '%s\n' "$mnt_out" | grep -E '^(ro|rw)(,|$)' | head -n 1)"
if [ "$rc" -ne 0 ]; then
  fail "could not read /proc/mounts in the sandbox (exit $rc)"
elif [ -z "$mnt_opts" ]; then
  if [ "$RECORDED_MOUNT" = 1 ]; then
    fail "/sandbox/projects is recorded as a host mount but is not mounted"
  else
    warn "/sandbox/projects is not mounted (plan §12.3 fallback: pass excerpts in each run)"
  fi
elif [[ "$mnt_opts" == ro* ]]; then
  pass "/sandbox/projects is mounted read-only ($mnt_opts)"
else
  fail "/sandbox/projects is mounted READ-WRITE ($mnt_opts); the agent could modify evidence"
fi

# ---------------------------------------------------------------------------
section "6. Host port forwards (openshell forward list)"
fwd_txt="$(timeout 30 openshell forward list 2>&1 | clean)"
for port in "${FORWARD_PORTS[@]}"; do
  if printf '%s\n' "$fwd_txt" | grep -Eq "(^|[^0-9])${port}([^0-9]|$)"; then
    pass "port $port listed by openshell forward list"
    continue
  fi
  svc="$(pgrep -af -- "forward service ${SANDBOX} " 2>/dev/null | grep -E -- "--target-port ${port}( |$)" | head -n 1)"
  listen="$(ss -ltnH 2>/dev/null | awk -v p=":${port}" 'substr($4, length($4) - length(p) + 1) == p { print $4 }' | head -n 1)"
  if [ -n "$svc" ] && [ "$listen" = "127.0.0.1:${port}" ]; then
    pass "port $port served on 127.0.0.1 by 'openshell forward service $SANDBOX' (not shown by forward list)"
  elif [ -n "$listen" ] && [ "$listen" != "127.0.0.1:${port}" ]; then
    warn "port $port listens on $listen, expected 127.0.0.1 only (plan §19)"
  elif [ -n "$listen" ]; then
    warn "port $port is listening but not via an OpenShell forward for $SANDBOX; confirm it is not the host ~/.hermes install"
  else
    fail "no forward for port $port (see 'nemohermes $SANDBOX status'; 'nemohermes $SANDBOX recover' repairs forwards, run it yourself)"
  fi
done

# ---------------------------------------------------------------------------
section "7. Slack reachability from the host"
if curl -fsS -m 15 https://slack.com/api/api.test 2>/dev/null | grep -q '"ok":true'; then
  pass "https://slack.com/api/api.test reachable"
else
  warn "https://slack.com/api/api.test unreachable (venue Wi-Fi? Socket Mode will drop)"
fi

if [ "$SLACK_REGISTERED" = 1 ]; then
  section "8. Recent Slack log lines (info only)"
  nh "$SANDBOX" logs --tail 400 | grep -i 'slack' | tail -n 6 | indent
fi

# ---------------------------------------------------------------------------
if [ -n "$POST_CHANNEL" ]; then
  section "Test post (--post $POST_CHANNEL)"
  if [ "$SLACK_REGISTERED" != 1 ]; then
    fail "Slack is not registered; not sending"
  else
    post_out="$(nh "$SANDBOX" exec -- hermes send --to "slack:${POST_CHANNEL}" "ClaimTrace is online")"
    rc=$?
    if [ "$rc" -eq 0 ]; then
      pass "sent 'ClaimTrace is online' to slack:${POST_CHANNEL}"
    else
      fail "hermes send exited $rc"
      printf '%s\n' "$post_out" | tail -n 5 | indent
    fi
  fi
fi

# ---------------------------------------------------------------------------
section "Summary"
printf '  %d PASS, %d WARN, %d FAIL\n' "$PASS_N" "$WARN_N" "$FAIL_N"

cat <<EOF

Manual checks (in Slack):
  1. DM the bot "ping" from an allowlisted account (SLACK_ALLOWED_USERS). Expect a reply;
     the first local-model reply can take a while.
  2. In #claimtrace, post "@<bot name> ping". Expect a reply. The bot must be invited
     (/invite @<bot name>) and the channel ID must be in SLACK_ALLOWED_CHANNELS.
  3. From an account NOT in the allowlist, DM or @mention the bot. Expect no agent answer
     (NemoClaw may send a short denial notice for channel mentions).
  4. If the bot stays silent: nemohermes $SANDBOX logs --follow
     Then check the Slack app: Socket Mode on, events app_mention, message.im,
     message.channels, message.groups, and the Messages tab enabled.
EOF

if [ "$FAIL_N" -gt 0 ]; then
  exit 1
fi
exit 0
