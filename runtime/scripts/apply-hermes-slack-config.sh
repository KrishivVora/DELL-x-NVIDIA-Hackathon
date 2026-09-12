#!/usr/bin/env bash
# apply-hermes-slack-config.sh
#
# Applies ClaimTrace's Slack behavior settings to the Hermes Agent running in a
# NemoClaw sandbox (default: my-hermes). Background and evidence:
# runtime/docs/hermes-slack-config.md
#
# How it works:
# - Each key is written with `nemohermes <sandbox> config set` from the host.
#   NemoClaw records those keys and replays them on `nemohermes <sandbox> rebuild`.
#   Edits made with `hermes config set` inside the sandbox are lost on rebuild.
# - Hermes reads the changes after `nemohermes <sandbox> gateway restart`.
#
# The default is a dry run that changes nothing. Pass --apply to write and restart.

set -euo pipefail

NVM_NODE_BIN="${HOME}/.nvm/versions/node/v22.23.2/bin"
if ! command -v nemohermes >/dev/null 2>&1 && [ -d "$NVM_NODE_BIN" ]; then
  PATH="${NVM_NODE_BIN}:${PATH}"
  export PATH
fi
# Only override this to point the script at a test stub.
NEMOHERMES="${NEMOHERMES:-nemohermes}"

SANDBOX="my-hermes"
CHANNEL=""
SKILL=""
DMS=()
APPLY=0

usage() {
  cat <<'EOF'
Usage: apply-hermes-slack-config.sh --channel <C...> [options]

Applies ClaimTrace Slack settings to the sandboxed Hermes Agent with
`nemohermes <sandbox> config set` (replayed by rebuild), then restarts the
Hermes gateway. Without --apply it only prints what would change.

Required:
  --channel <C...>   Slack channel ID of #claimtrace (not the #name)

Options:
  --skill <names>    Bind these skills to the channel and to every --dm ID.
                     Comma-separated, e.g. research-assistant,claimtrace.
                     Pass every skill the channel should load: this key is
                     replaced, not merged, so a single name drops the others.
  --dm <D...>        DM channel ID that gets the same prompt and skill binding
                     (repeatable)
  --sandbox <name>   Sandbox name (default: my-hermes)
  --apply            Write the changes, then restart the Hermes gateway
  -h, --help         Show this help

Exit codes: 0 ok, 1 error, 2 usage error, 3 refused (Slack not set up yet).
EOF
}

usage_error() {
  printf 'error: %s\n\n' "$1" >&2
  usage >&2
  exit 2
}
die() {
  printf 'error: %s\n' "$1" >&2
  exit 1
}
refuse() {
  printf 'refusing: %s\n' "$1" >&2
  exit 3
}
ok() { printf '  ok    %s\n' "$1"; }
warn() { printf '  warn  %s\n' "$1"; }
need_value() {
  if [ -z "${2-}" ]; then
    usage_error "$1 needs a value"
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --channel)
      need_value "$1" "${2-}"
      CHANNEL="$2"
      shift 2
      ;;
    --channel=*)
      CHANNEL="${1#*=}"
      shift
      ;;
    --skill)
      need_value "$1" "${2-}"
      SKILL="$2"
      shift 2
      ;;
    --skill=*)
      SKILL="${1#*=}"
      shift
      ;;
    --dm)
      need_value "$1" "${2-}"
      DMS+=("$2")
      shift 2
      ;;
    --dm=*)
      DMS+=("${1#*=}")
      shift
      ;;
    --sandbox)
      need_value "$1" "${2-}"
      SANDBOX="$2"
      shift 2
      ;;
    --sandbox=*)
      SANDBOX="${1#*=}"
      shift
      ;;
    --apply)
      APPLY=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage_error "unknown argument: $1"
      ;;
  esac
done

[ -n "$CHANNEL" ] || usage_error "--channel <C...> is required"
[[ "$CHANNEL" =~ ^[CG][A-Z0-9]{6,}$ ]] ||
  usage_error "--channel must be a Slack channel ID such as C0123ABCD, not a #name"
if [ -n "$SKILL" ] && ! [[ "$SKILL" =~ ^[a-z0-9][a-z0-9._-]{0,63}$ ]]; then
  usage_error "--skill must be a skill name such as claimtrace"
fi
for dm in "${DMS[@]}"; do
  [[ "$dm" =~ ^D[A-Z0-9]{6,}$ ]] || usage_error "--dm must be a Slack DM ID such as D0123ABCD"
done
[[ "$SANDBOX" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || usage_error "invalid --sandbox name: $SANDBOX"

command -v jq >/dev/null 2>&1 || die "jq is required"
command -v "$NEMOHERMES" >/dev/null 2>&1 ||
  die "nemohermes not found; run: export PATH=\"${NVM_NODE_BIN}:\$PATH\""

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

nh() { "$NEMOHERMES" "$@"; }
# nemohermes can print "Active gateway set to 'nemoclaw'" before command output.
strip_banner() { grep -v 'Active gateway set to' || true; }
# Single-quote a string for display as a shell argument.
shq() {
  local s=${1//\'/\'\\\'\'}
  printf "'%s'" "$s"
}
short() {
  local s="$1"
  if [ "${#s}" -gt 100 ]; then
    printf '%s...' "${s:0:100}"
  else
    printf '%s' "$s"
  fi
}

# Prints the key's current value as sorted compact JSON, or UNSET when the key
# does not exist. Read-only (`config get` redacts credentials).
current_value() {
  local key="$1" out rc=0
  out="$(nh "$SANDBOX" config get --key "$key" --format json 2>"$WORK/get.err")" || rc=$?
  if [ "$rc" -ne 0 ]; then
    if grep -q 'not found in' "$WORK/get.err"; then
      printf 'UNSET'
      return 0
    fi
    cat "$WORK/get.err" >&2
    return 1
  fi
  if ! printf '%s\n' "$out" | strip_banner | jq -cS . 2>/dev/null; then
    printf 'error: could not parse "config get" output for %s\n' "$key" >&2
    return 1
  fi
}

config_set_with_retry() {
  local key="$1" value="$2" attempt out
  for attempt in 1 2 3; do
    if out="$(nh "$SANDBOX" config set --key "$key" --value "$value" --config-accept-new-path 2>&1)"; then
      printf '%s\n' "$out" | strip_banner | sed 's/^/    /'
      return 0
    fi
    # NemoClaw reports these two as retryable (runtime-controls.mdx).
    if [ "$attempt" -lt 3 ] && grep -qE 'already in progress|SUPERVISOR_BUSY' <<<"$out"; then
      printf '    busy, retrying in 10s (attempt %d of 3)\n' "$attempt"
      sleep 10
      continue
    fi
    printf '%s\n' "$out" | strip_banner | sed 's/^/    /' >&2
    return 1
  done
  return 1
}

if [ "$APPLY" -eq 1 ]; then
  MODE_LABEL="APPLY"
else
  MODE_LABEL="DRY RUN, nothing will be changed"
fi
printf 'ClaimTrace Hermes Slack settings for sandbox %s (%s)\n\n' "$SANDBOX" "$MODE_LABEL"

# ---------------------------------------------------------------------------
# Preflight: Slack must already be connected through NemoClaw.
# ---------------------------------------------------------------------------
printf 'Checks\n'
status_out="$(nh "$SANDBOX" channels status --json 2>"$WORK/status.err")" ||
  die "'nemohermes $SANDBOX channels status --json' failed: $(cat "$WORK/status.err")"
status_json="$(printf '%s\n' "$status_out" | sed -n '/^[[:space:]]*{/,$p')"
printf '%s' "$status_json" | jq -e . >/dev/null 2>&1 ||
  die "could not parse 'nemohermes $SANDBOX channels status --json' output"
if ! printf '%s' "$status_json" |
  jq -e '[.channels[]?.channel, .channel] | any(.[]; . == "slack")' >/dev/null; then
  refuse "Slack is not configured in sandbox '$SANDBOX' ('nemohermes $SANDBOX channels status' lists no slack channel). Connect Slack first (plan section 12.3, or 'nemohermes $SANDBOX channels add slack'), then re-run."
fi
ok "Slack channel is registered (nemohermes $SANDBOX channels status)"
if printf '%s' "$status_json" |
  jq -e '[.. | objects | .detail? | strings | select(test("paused"))] | length > 0' >/dev/null 2>&1; then
  warn "Slack looks paused; settings can still be written, but Slack stays off until 'nemohermes $SANDBOX channels start slack'"
fi

slack_cfg="$(current_value platforms.slack)" || die "could not read platforms.slack from the Hermes config"
if [ "$slack_cfg" = "UNSET" ] || ! printf '%s' "$slack_cfg" | jq -e '.enabled == true' >/dev/null; then
  refuse "Slack is registered with NemoClaw, but platforms.slack.enabled is not true in /sandbox/.hermes/config.yaml. Finish the rebuild ('nemohermes $SANDBOX rebuild'), then re-run."
fi
ok "platforms.slack.enabled is true in the Hermes config"

if [ -n "$SKILL" ]; then
  skills_out="$(nh "$SANDBOX" skill list 2>/dev/null || true)"
  if grep -qE "(^|[^A-Za-z0-9_.-])${SKILL}([^A-Za-z0-9_.-]|$)" <<<"$skills_out"; then
    ok "skill '$SKILL' is installed"
  else
    warn "skill '$SKILL' is not in 'nemohermes $SANDBOX skill list'; install it first, or Hermes logs \"Auto-skill not found\" and skips the binding"
  fi
fi

# ---------------------------------------------------------------------------
# Desired settings. Keep the channel prompt free of URLs: config set rejects
# private URLs, and the skill already knows the worker API address.
# ---------------------------------------------------------------------------
PROMPT_TEXT='You are Labmate, the research team local assistant, answering in Slack. Projects live under /sandbox/projects; if no project is named, use demo-001. For questions about a project use the research-assistant skill and its labmate ask command; to check a claim or number use the claimtrace skill and labmate verify. Only labmate commands read project data. Never search the web. Reply in a few lines and cite project-relative file paths and page numbers. Quote at most one short sentence, never paste rows, tables or values from data files, never attach files, and never ask for research files. Never state a claim status that labmate has not recorded. Run labmate with the terminal tool. If terminal is not in your visible tools, call tool_search for it and use it; never wrap labmate in execute_code and never read project files with python. Always run labmate ask before answering a project question, even when you think you know the answer. Only if labmate returns no usable passage may you answer from general knowledge, and then begin the reply with "Not from project files:".'

ids_json="$(jq -cn --arg c "$CHANNEL" '[$c] + $ARGS.positional' --args "${DMS[@]}")"
prompts_value="$(jq -cS -n --argjson ids "$ids_json" --arg p "$PROMPT_TEXT" \
  '[$ids[] | {key: ., value: $p}] | from_entries')"
bindings_value="$(jq -cS -n --argjson ids "$ids_json" --arg s "$SKILL" \
  '($s | split(",")) as $skills | [$ids[] | {id: ., skills: $skills}]')"
suggested_value="$(jq -cS -n '{
  title: "ClaimTrace",
  prompts: [
    {title: "Status of demo-001", message: "What is the status of demo-001?"},
    {title: "Why is claim-003 conflicting?", message: "Why is claim-003 in demo-001 conflicting?"},
    {title: "Re-audit demo-001", message: "Re-audit demo-001"},
    {title: "What needs attention?", message: "Which claims in demo-001 need attention?"}
  ]
}')"

KEYS=()
VALUES=()
WHY=()
add_setting() {
  KEYS+=("$1")
  VALUES+=("$(printf '%s' "$2" | jq -cS .)")
  WHY+=("$3")
}

add_setting platforms.slack.extra.require_mention 'true' \
  "channel messages need an @mention, so chatter does not use model slots (plan 18, rules 4 and 17)"
add_setting platforms.slack.extra.reply_in_thread 'true' \
  "answers stay in the asker's thread; alerts and digests stay top-level (plan 17.4)"
add_setting platforms.slack.extra.unauthorized_dm_behavior '"ignore"' \
  "DMs from non-allowlisted users get silence, not a pairing code (plan 16.3 step 4)"
add_setting platforms.slack.extra.allow_bots '"none"' \
  "posts by other bots and apps never become prompts (plan 16.6)"
add_setting platforms.slack.extra.channel_prompts "$prompts_value" \
  "restates the sanitized-reply rules on every turn (plan 15.5, 16.5)"
if [ -n "$SKILL" ]; then
  add_setting platforms.slack.extra.channel_skill_bindings "$bindings_value" \
    "auto-loads the $SKILL skill when a session starts (plan 15.5)"
fi
add_setting platforms.slack.extra.suggested_prompts "$suggested_value" \
  "one-click demo questions in the assistant DM pane (plan 16.1)"
add_setting display.platforms.slack.live_status '"verb"' \
  "status line shows the verb only, without file paths or commands (plan 16.5)"
add_setting display.platforms.slack.tool_progress '"off"' \
  "overrides NemoClaw's global tool_progress=all, which posts tool calls with argument previews (plan 16.5)"
add_setting display.platforms.slack.interim_assistant_messages 'false' \
  "no mid-turn commentary that could quote file contents; one final reply (plan 16.5)"
# Global, not Slack-only: NemoClaw builds sandboxes with progressive tool
# disclosure, so only part of the catalog is visible per session and the model
# reaches the CLI through execute_code wrappers (one scary approval each).
# With it off, sessions see the terminal tool directly. The durable equivalent
# is `nemohermes <sandbox> rebuild --tool-disclosure direct`.
add_setting tools.tool_search.enabled 'false' \
  "show the whole tool catalog, so the agent runs labmate with the terminal tool instead of execute_code"

# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------
printf '\nPlanned changes (each through nemohermes config set; rebuild replays these keys)\n'
CHANGED=()
for i in "${!KEYS[@]}"; do
  key="${KEYS[$i]}"
  want="${VALUES[$i]}"
  cur="$(current_value "$key")" || die "could not read $key"
  if [ "$cur" = "$want" ]; then
    printf '\n  [same]   %s = %s\n' "$key" "$(short "$want")"
    continue
  fi
  CHANGED+=("$i")
  if [ "$cur" = "UNSET" ]; then
    cur_display="(unset)"
  else
    cur_display="$cur"
  fi
  printf '\n  [change] %s\n' "$key"
  printf '      current: %s\n' "$cur_display"
  printf '      new:     %s\n' "$want"
  printf '      why:     %s\n' "${WHY[$i]}"
  printf '      command: nemohermes %s config set --key %s --value %s --config-accept-new-path\n' \
    "$SANDBOX" "$key" "$(shq "$want")"
done

printf '\nNot managed by this script: platforms.slack.extra.rich_blocks (NemoClaw renders true),\n'
printf 'SLACK_ALLOWED_USERS and SLACK_ALLOWED_CHANNELS (.env, set through channels add slack),\n'
printf 'agent.reasoning_effort (team decision).\n'

if [ "${#CHANGED[@]}" -eq 0 ]; then
  printf '\nNothing to change, so the Hermes gateway needs no restart.\n'
  exit 0
fi

printf '\nAfter the writes, the gateway restarts so Hermes reloads config.yaml:\n'
printf '  nemohermes %s gateway restart\n' "$SANDBOX"

if [ "$APPLY" -ne 1 ]; then
  printf '\n%d change(s) planned. Re-run with --apply to write them and restart the gateway.\n' "${#CHANGED[@]}"
  exit 0
fi

# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------
printf '\nApplying %d change(s)\n' "${#CHANGED[@]}"
for i in "${CHANGED[@]}"; do
  key="${KEYS[$i]}"
  printf '\n  config set %s\n' "$key"
  config_set_with_retry "$key" "${VALUES[$i]}" ||
    die "config set failed for $key. Keys before it were written; fix the error and re-run (matching keys are skipped)."
done

printf '\n  gateway restart\n'
nh "$SANDBOX" gateway restart ||
  die "the config was written but the gateway restart failed; run: nemohermes $SANDBOX gateway restart"

printf '\nVerifying\n'
failed=0
for i in "${!KEYS[@]}"; do
  key="${KEYS[$i]}"
  if ! cur="$(current_value "$key")"; then
    warn "$key could not be read back"
    failed=1
    continue
  fi
  if [ "$cur" = "${VALUES[$i]}" ]; then
    ok "$key"
  else
    warn "$key does not match (got $(short "$cur"))"
    failed=1
  fi
done
[ "$failed" -eq 0 ] || die "some keys did not verify"

printf '\nDone. Next: DM the bot "ping", @mention it in the channel, and check that no\n'
printf 'tool-progress lines appear. After a rebuild, run this script without --apply;\n'
printf 'it should report nothing to change.\n'
