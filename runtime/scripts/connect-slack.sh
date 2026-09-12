#!/usr/bin/env bash
# connect-slack.sh: connect Slack to the NemoClaw-managed Hermes sandbox "my-hermes".
#
# Run it in YOUR OWN terminal on the GB10, ideally inside tmux. Onboarding is interactive
# and cannot be driven from a pipe or from an agent's non-TTY shell.
#
# Usage:
#   runtime/scripts/connect-slack.sh [--rebuild-with-mount | --channels-add]
#
#   --rebuild-with-mount  (default; CLAIMTRACE-PROJECT-PLAN.md §12.3 option A)
#       nemohermes onboard --fresh --name my-hermes --recreate-sandbox \
#         --host-mount /home/dell/claimtrace-data/projects:/sandbox/projects
#   --channels-add        (§12.3 option B: Slack only, no host mount)
#       nemohermes my-hermes channels add slack
#
# Afterwards run runtime/scripts/verify-slack.sh.
#
# The script never prints the Slack tokens and unsets them on exit. NemoClaw stores them
# in its OpenShell credential store; never copy them into files or the repo.
#
# ---------------------------------------------------------------------------------------
# Verified 2026-09-12 against NemoClaw v0.0.123 (lkg f75f722), OpenShell 0.0.106, Hermes
# Agent v0.20.6 in the sandbox. Paths are relative to gb10-offline-bundle/NemoClaw/.
# Nothing here was exercised live: onboard and channels add were NOT run.
#
# 1. Does a non-installer onboard show the DGX Spark inference choice?  No.
#    - "Choose the DGX Spark inference setup: 1) Managed vLLM ... 2) Qwen3.6 35B-A3B NVFP4
#      with the fixed catalog-backed vLLM profile" and "Run express install with these
#      settings?" exist only in the installer (scripts/install.sh:5980-5982, :6713).
#    - Choice 2 only exports NEMOCLAW_ENABLE_LOCAL_MODEL_PROFILE=1 and
#      NEMOCLAW_LOCAL_MODEL_RUNTIME=vllm together with NEMOCLAW_NON_INTERACTIVE=1
#      (scripts/install.sh:6012-6028). That path aborts in interactive onboarding: "The local
#      model profile requires non-interactive onboarding." (src/lib/onboard/setup-nim-flow.ts:603-604).
#    - `nemohermes onboard` shows the generic "Select your inference provider" menu
#      (src/lib/onboard/provider-selection-prompt.ts:37-57). Its default is "build" (NVIDIA
#      Endpoints, a CLOUD provider) unless NEMOCLAW_PROVIDER names a menu key
#      (provider-selection-prompt.ts:16-24). Pressing Enter blindly would pick cloud inference.
#
# 2. Can env vars preselect the same local vLLM?  Yes, NEMOCLAW_PROVIDER=vllm. Nothing else.
#    - The first install recorded provider vllm-local and model nvidia/Qwen3.6-35B-A3B-NVFP4,
#      served by the managed container nemoclaw-vllm on 127.0.0.1:8000 (non-secret fields of
#      ~/.nemoclaw/onboard-session.json and host-local-vllm-runtime.json).
#    - With that container running, the menu has one vLLM entry, key "vllm":
#      "Local vLLM (localhost:8000) — running (suggested)" (src/lib/onboard/vllm-menu.ts:76-90).
#      Selecting it sets provider vllm-local (src/lib/onboard/setup-nim-vllm.ts:258), uses the
#      managed endpoint binding (:289), and reads the model from the server (:385). Nothing is
#      downloaded or restarted. "vllm" is a valid provider key
#      (src/lib/onboard/inference-providers/provider-selection-keys.ts:7, :31).
#    - The code reads NEMOCLAW_PROVIDER in interactive mode too
#      (src/lib/onboard/setup-nim-provider-discovery.ts:138 -> setup-nim-flow.ts:1072). That
#      picks the running local vLLM without the menu (src/lib/onboard/provider-selection.ts:246-249)
#      and fails closed if it is absent (:283-290). A test title says interactive onboarding
#      ignores NEMOCLAW_PROVIDER (src/lib/onboard/host-dns-preflight.test.ts:341). Even then
#      the variable makes Local vLLM the menu DEFAULT (provider-selection-prompt.ts:17-22).
#      Both readings end at local vLLM.
#    - Do NOT use NEMOCLAW_PROVIDER=install-vllm: with vLLM already running it stops with a port
#      conflict (setup-nim-flow.ts:1320-1330). Do NOT set NEMOCLAW_MODEL, NEMOCLAW_VLLM_MODEL or
#      NEMOCLAW_SERVING_PRESET: a mismatch aborts (setup-nim-vllm.ts:338-375). They and the
#      local-model-profile gates are removed from the onboard environment below.
#
# 3. Do exported SLACK_* vars preselect Slack in the messaging picker?  Yes.
#    - The interactive picker pre-selects every channel whose required inputs resolve from the
#      environment or credential store (src/lib/onboard/messaging-channel-setup.ts:235;
#      src/lib/messaging/utils.ts:107-157). Slack requires SLACK_BOT_TOKEN and SLACK_APP_TOKEN
#      (src/lib/messaging/channels/slack/manifest.ts:15-40), so Slack shows "●" and "(configured)".
#    - Exported tokens are reused without a prompt
#      (src/lib/messaging/hooks/common/token-paste.ts:114-126). Exported SLACK_ALLOWED_USERS and
#      SLACK_ALLOWED_CHANNELS are reused and logged as "already set"
#      (src/lib/messaging/hooks/common/config-prompt.ts:66, :148, :240).
#    - Tokens are checked live with slack.com/api/auth.test and apps.connections.open
#      (src/lib/messaging/channels/slack/hooks/credential-validation.ts:29-30). If Slack rejects
#      them, the Slack channel is SKIPPED and onboarding carries on (slack/manifest.ts:268-274;
#      docs/manage-sandboxes/set-up-slack.mdx:24-25).
#    - For Hermes, selected messaging presets (slack) are included in the create-time policy
#      (src/lib/onboard/initial-policy.ts:513-515). Only one Slack sandbox may run per OpenShell
#      gateway (set-up-slack.mdx:55-59).
#
# 4. Is --host-mount read-only and is the syntax right?  Yes and yes.
#    - `nemohermes onboard --help`: "Expose an existing absolute host directory read-only below
#      /sandbox (repeatable)". Format HOST_DIRECTORY:/sandbox/DIRECTORY; the host path must be
#      absolute, exist, be a directory, and contain no symlinks; the target must be normalized and
#      below /sandbox; the mount is recorded readOnly: true (src/lib/state/registry/host-mount.ts:48-83).
#    - Status then prints "Host mounts: <src> -> <target> (read-only)"
#      (src/lib/actions/sandbox/status-text.ts:171-176).
#
# 5. What --recreate-sandbox does.
#    - It skips the "Reuse existing sandbox?" prompt (src/lib/onboard/sandbox-create/orchestration.ts:2066,
#      :2152-2154). It backs up workspace state first and aborts if that fails (:2240-2256).
#      It then passes that backup to the new sandbox as restoreBackupPath (:2554-2555).
#    - Backed-up Hermes dirs include memories, sessions, skills, cron and scripts
#      (agents/hermes/manifest.yaml:70-90). Treat the restore as best effort.
#    - --name skips the sandbox-name prompt (src/lib/onboard/entry-options.ts:382-389).
#
# 6. Other onboarding prompts (the order may differ).
#    - Web search: "[1] No web search (default)" (src/lib/onboard/web-search-flow.ts:350-358).
#    - Resource profile: the default is "No profile" (src/lib/onboard/resource-profile-selection.ts:78-90).
#    - Policy tier: the default is Balanced (src/lib/onboard/policy-selection-prompts.ts:129-131),
#      followed by a preset list.
#    - "Hermes managed Nous tools" appears only for the Nous provider
#      (src/lib/onboard/hermes-managed-tools.ts:278).
#    - The third-party notice is already accepted: ~/.nemoclaw/usage-notice.json has version
#      2026-04-01b, matching bin/lib/usage-notice.json:2.
#
# 7. `channels add slack` (option B) reuses exported tokens, applies the slack preset, and asks
#    whether to rebuild (docs/manage-sandboxes/add-channels-after-onboarding.mdx:50, :71-99).
#    It cannot add a host mount.
# ---------------------------------------------------------------------------------------

set -euo pipefail
set +x

SANDBOX="my-hermes"
MOUNT_SRC="/home/dell/claimtrace-data/projects"
MOUNT_DST="/sandbox/projects"
NODE_BIN="$HOME/.nvm/versions/node/v22.23.2/bin"
EXPECTED_NEMOCLAW_VERSION="v0.0.123"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="rebuild-with-mount"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--rebuild-with-mount | --channels-add] [-h|--help]

  --rebuild-with-mount  (default) Recreate sandbox '$SANDBOX' with Slack and the read-only
                        host mount $MOUNT_SRC -> $MOUNT_DST.
                        Runs: nemohermes onboard --fresh --name $SANDBOX --recreate-sandbox \\
                                --host-mount $MOUNT_SRC:$MOUNT_DST
  --channels-add        Add Slack to the existing sandbox (no host mount).
                        Runs: nemohermes $SANDBOX channels add slack
  -h, --help            Show this help.

Tokens: SLACK_BOT_TOKEN (xoxb-...) and SLACK_APP_TOKEN (xapp-...) are read silently unless
already exported. Allowlists: SLACK_ALLOWED_USERS (U.../W...) and SLACK_ALLOWED_CHANNELS (C.../G...).
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --rebuild-with-mount) MODE="rebuild-with-mount" ;;
    --channels-add) MODE="channels-add" ;;
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
  shift
done

if [ -t 1 ]; then
  C_G=$'\033[32m' C_Y=$'\033[33m' C_R=$'\033[31m' C_B=$'\033[1m' C_0=$'\033[0m'
else
  C_G="" C_Y="" C_R="" C_B="" C_0=""
fi
step() { printf '\n%s==> %s%s\n' "$C_B" "$*" "$C_0"; }
ok() { printf '  %s✓%s %s\n' "$C_G" "$C_0" "$*"; }
warn() { printf '  %s!%s %s\n' "$C_Y" "$C_0" "$*" >&2; }
die() {
  printf '  %s✗ %s%s\n' "$C_R" "$*" "$C_0" >&2
  exit 1
}

cleanup() {
  unset SLACK_BOT_TOKEN SLACK_APP_TOKEN
}
trap cleanup EXIT
trap 'printf "\nInterrupted.\n" >&2; exit 130' INT TERM

trim() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "$s"
}

# read_secret VAR LABEL REGEX HINT: use exported VAR if valid, else read it silently.
read_secret() {
  local name="$1" label="$2" re="$3" hint="$4"
  local value="${!name:-}" attempt
  if [ -n "$value" ]; then
    [[ "$value" =~ $re ]] || die "$name is set in the environment but is not a valid token ($hint). Unset it and rerun."
    ok "$name taken from the environment (not shown)"
  else
    for attempt in 1 2 3; do
      printf '  %s (input hidden): ' "$label"
      IFS= read -rs value || die "No input for $name."
      printf '\n'
      value="$(trim "$value")"
      if [[ "$value" =~ $re ]]; then
        break
      fi
      warn "That is not a valid token ($hint). Attempt $attempt of 3."
      value=""
    done
    [ -n "$value" ] || die "No valid $name entered."
    ok "$name accepted (not shown)"
  fi
  printf -v "$name" '%s' "$value"
  export "${name?}"
}

# read_id_list VAR LABEL ID_REGEX REQUIRED(1|0) HINT: comma-separated Slack IDs.
read_id_list() {
  local name="$1" label="$2" re="$3" required="$4" hint="$5"
  local current="${!name:-}" value id all_ok
  local -a ids
  while true; do
    if [ -n "$current" ]; then
      printf '  %s [%s]: ' "$label" "$current"
    else
      printf '  %s: ' "$label"
    fi
    IFS= read -r value || die "No input for $name."
    value="${value:-$current}"
    value="$(printf '%s' "$value" | tr -d '[:space:]')"
    value="${value#,}"
    value="${value%,}"
    if [ -z "$value" ]; then
      if [ "$required" = 1 ]; then
        warn "$name is required ($hint)."
        continue
      fi
      break
    fi
    all_ok=1
    IFS=',' read -ra ids <<<"$value"
    for id in "${ids[@]}"; do
      if ! [[ "$id" =~ $re ]]; then
        warn "'$id' is not a valid ID ($hint)."
        all_ok=0
      fi
    done
    if [ "$all_ok" = 1 ]; then
      break
    fi
    current=""
  done
  printf -v "$name" '%s' "$value"
  export "${name?}"
}

# ---------------------------------------------------------------------------------------
step "Preflight (mode: $MODE)"

[ -t 0 ] && [ -t 1 ] || die "Run this in an interactive terminal (TTY). Onboarding prompts cannot be answered from a pipe."

export PATH="$NODE_BIN:$PATH"
command -v nemohermes >/dev/null 2>&1 || die "nemohermes not found (expected in $NODE_BIN)."
ok "nemohermes: $(command -v nemohermes)"
for tool in curl python3 timeout; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is required."
done

nh_version="$(nemohermes --version 2>/dev/null | grep -Eo 'v[0-9]+\.[0-9]+\.[0-9]+' | head -n 1 || true)"
if [ "$nh_version" = "$EXPECTED_NEMOCLAW_VERSION" ]; then
  ok "NemoClaw $nh_version"
else
  warn "NemoClaw is '${nh_version:-unknown}', not $EXPECTED_NEMOCLAW_VERSION. The wizard guidance below was researched against $EXPECTED_NEMOCLAW_VERSION."
fi

list_json="$(timeout 60 nemohermes list --json 2>/dev/null || true)"
if ! printf '%s' "$list_json" | python3 -c '
import json, sys
raw = sys.stdin.read()
i = raw.find("{")
try:
    d, _ = json.JSONDecoder().raw_decode(raw[i:]) if i >= 0 else ({}, 0)
except ValueError:
    sys.exit(1)
sys.exit(0 if any(s.get("name") == sys.argv[1] for s in d.get("sandboxes") or []) else 1)
' "$SANDBOX"; then
  die "Sandbox '$SANDBOX' was not found in 'nemohermes list'."
fi
ok "sandbox '$SANDBOX' exists"

if curl -fsS -m 15 https://slack.com/api/api.test 2>/dev/null | grep -q '"ok":true'; then
  ok "https://slack.com/api/api.test is reachable"
else
  die "https://slack.com/api/api.test is not reachable. NemoClaw validates the tokens live against Slack, so fix the network first."
fi

if [ "$MODE" = "rebuild-with-mount" ]; then
  vllm_code="$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:8000/v1/models 2>/dev/null || true)"
  if [ -z "$vllm_code" ] || [ "$vllm_code" = "000" ]; then
    die "Local vLLM is not answering on 127.0.0.1:8000. Without it the wizard cannot offer 'Local vLLM — running'. Check 'docker ps' for nemoclaw-vllm first."
  fi
  ok "local vLLM is listening on 127.0.0.1:8000 (HTTP $vllm_code)"

  mkdir -p "$MOUNT_SRC"
  [ -d "$MOUNT_SRC" ] || die "$MOUNT_SRC is not a directory."
  [ "$(readlink -f "$MOUNT_SRC")" = "$MOUNT_SRC" ] || die "$MOUNT_SRC contains a symbolic link; NemoClaw rejects symlinked host mounts."
  ok "host mount source ready: $MOUNT_SRC"
  secret_hit="$(find "$MOUNT_SRC" -maxdepth 4 \( -name '*.env' -o -name '.env*' -o -name '*.pem' -o -name '*.key' -o -iname '*token*' \) -print -quit 2>/dev/null || true)"
  if [ -n "$secret_hit" ]; then
    warn "Credential-like file under the mount: $secret_hit. Every sandbox process can read it; move it out first."
  fi

  if [ -z "${TMUX:-}" ] && [ -z "${STY:-}" ]; then
    warn "You are not inside tmux/screen. If this is an SSH session, a dropped connection can kill the rebuild (Ctrl+C now and run: tmux new -s slack)."
  fi
fi

# Remove variables that would change the inference choice or silently skip prompts.
# These names were confirmed in the NemoClaw source (see header).
CHILD_UNSET_VARS=(
  NEMOCLAW_PROVIDER NEMOCLAW_PROVIDER_MODEL NEMOCLAW_MODEL NEMOCLAW_VLLM_MODEL
  NEMOCLAW_VLLM_EXTRA_ARGS_JSON NEMOCLAW_SERVING_PRESET NEMOCLAW_ENABLE_LOCAL_MODEL_PROFILE
  NEMOCLAW_LOCAL_MODEL_RUNTIME NEMOCLAW_NON_INTERACTIVE NEMOCLAW_YES NEMOCLAW_SANDBOX_NAME
  NEMOCLAW_RECREATE_SANDBOX NEMOCLAW_RECREATE_WITHOUT_BACKUP NEMOCLAW_SKIP_SLACK_AUTH_VALIDATION
  NEMOCLAW_POLICY_MODE NEMOCLAW_RESOURCE_PROFILE
  TELEGRAM_BOT_TOKEN DISCORD_BOT_TOKEN WECHAT_BOT_TOKEN WHATSAPP_ALLOWED_IDS
  MSTEAMS_APP_ID MSTEAMS_APP_PASSWORD MSTEAMS_TENANT_ID
)
present_vars=()
for v in "${CHILD_UNSET_VARS[@]}"; do
  if [ -n "${!v+x}" ]; then
    present_vars+=("$v")
  fi
done
if [ "${#present_vars[@]}" -gt 0 ]; then
  warn "Not passing these exported variables to NemoClaw: ${present_vars[*]}"
fi
env_args=()
for v in "${CHILD_UNSET_VARS[@]}"; do
  env_args+=(-u "$v")
done

# ---------------------------------------------------------------------------------------
step "Slack credentials and allowlists"
cat <<'EOF'
  Bot token:  Slack app -> OAuth & Permissions -> Bot User OAuth Token (xoxb-...)
  App token:  Slack app -> Basic Information -> App-Level Tokens, scope connections:write (xapp-...)
  Member IDs: each allowed person's profile -> More -> Copy member ID (U...). Not the bot's ID.
  Channel ID: #claimtrace -> channel details -> bottom of the About tab (C...).
EOF
read_secret SLACK_BOT_TOKEN "Slack Bot Token" '^xoxb-[A-Za-z0-9_-]+$' "starts with xoxb-"
read_secret SLACK_APP_TOKEN "Slack App Token (Socket Mode)" '^xapp-[A-Za-z0-9_-]+$' "starts with xapp-"
read_id_list SLACK_ALLOWED_USERS "SLACK_ALLOWED_USERS (comma-separated member IDs, required)" \
  '^[UW][A-Z0-9]{2,}$' 1 "member IDs start with U or W, e.g. U01ABC2DEF3"
read_id_list SLACK_ALLOWED_CHANNELS "SLACK_ALLOWED_CHANNELS (comma-separated channel IDs)" \
  '^[CG][A-Z0-9]{2,}$' 0 "channel IDs start with C or G, e.g. C012AB3CD"
if [ -z "${SLACK_ALLOWED_CHANNELS:-}" ]; then
  warn "No channel allowlist: the bot would answer allowlisted users' @mentions in ANY channel it is in (plan §19 wants the private #claimtrace ID)."
  printf '  Continue without a channel allowlist? [y/N]: '
  IFS= read -r reply || die "No input."
  case "$reply" in
    y | Y | yes | YES) ;;
    *) die "Stopped. Rerun and enter the #claimtrace channel ID." ;;
  esac
fi
ok "SLACK_ALLOWED_USERS=$SLACK_ALLOWED_USERS"
ok "SLACK_ALLOWED_CHANNELS=${SLACK_ALLOWED_CHANNELS:-<none>}"

# ---------------------------------------------------------------------------------------
if [ "$MODE" = "rebuild-with-mount" ]; then
  step "Confirm the rebuild"
  cat <<EOF
  ${C_R}WARNING:${C_0} sandbox '$SANDBOX' will be DELETED and RECREATED.
    - Sandbox state (installed skills, cron jobs, sessions, memories) should be treated as LOST.
      NemoClaw backs up Hermes state first and tries to restore it, but do not count on that.
    - The model and container images are cached; nothing large is downloaded again.
    - The new sandbox gets Slack plus the read-only mount $MOUNT_SRC -> $MOUNT_DST.
    - Get team sign-off first (plan §12.3). Hermes API/dashboard on 8642/18789 go down meanwhile.
EOF
  printf '  Type the sandbox name (%s) to continue: ' "$SANDBOX"
  IFS= read -r answer || die "No input."
  [ "$answer" = "$SANDBOX" ] || die "Confirmation did not match. Nothing was changed."

  step "Answer the wizard like this (order may differ)"
  cat <<EOF
  1. Inference: this script sets NEMOCLAW_PROVIDER=vllm. Expect "Provider: vllm", or a menu
     whose default is "Local vLLM (localhost:8000) — running (suggested)". Choose that entry.
     NEVER choose NVIDIA Endpoints, OpenRouter, OpenAI, Anthropic, Gemini, or Hermes/Nous (all cloud).
     Expect "✓ Using managed vLLM endpoint" and "Detected model: nvidia/Qwen3.6-35B-A3B-NVFP4".
     Any other model: press Ctrl+C.
  2. Sandbox name: not asked (--name $SANDBOX).
  3. Web search: 1 (No web search).
  4. Messaging channels: "slack" should already show ● (configured). Press Enter.
     If another channel shows ●, press its number to turn it off first.
  5. Slack: expect "already configured" / "already set" lines and no token prompt. If you see
     ✗ or "skipping" for slack, validation failed: press Ctrl+C and check the tokens.
  6. Resource profile: press Enter (No profile).
  7. Policy tier: Balanced (the default). Presets: keep the suggestions; do not add brave,
     tavily or nous-*. The slack preset is added automatically for Hermes.
  8. "Hermes managed Nous tools" should not appear. If it does, answer none.
  9. Expect "Host directory access requested (read-only): $MOUNT_SRC -> $MOUNT_DST",
     then "Backing up workspace state..." and "Deleting and recreating sandbox '$SANDBOX'...".
EOF
  printf '\n  Press Enter to start onboarding (Ctrl+C to cancel): '
  IFS= read -r _ || die "No input."

  step "Running: nemohermes onboard --fresh --name $SANDBOX --recreate-sandbox --host-mount $MOUNT_SRC:$MOUNT_DST"
  set +e
  env "${env_args[@]}" NEMOCLAW_PROVIDER=vllm \
    nemohermes onboard --fresh --name "$SANDBOX" --recreate-sandbox \
    --host-mount "$MOUNT_SRC:$MOUNT_DST"
  rc=$?
  set -e
else
  step "Add Slack to the existing sandbox"
  cat <<EOF
  This registers Slack for '$SANDBOX' and then offers a rebuild so the running sandbox picks it up.
  It does NOT add the $MOUNT_DST host mount (that needs --rebuild-with-mount).

  Answer the prompts like this:
  1. Tokens and allowlists: reused from this script (no token prompt expected).
     If you see ✗ for slack, validation failed: press Ctrl+C and check the tokens.
  2. When asked whether to rebuild now, answer yes. The rebuild backs up and restores Hermes
     state, but a rebuild still restarts the sandbox (8642/18789 go down briefly).
EOF
  printf '\n  Press Enter to start (Ctrl+C to cancel): '
  IFS= read -r _ || die "No input."

  step "Running: nemohermes $SANDBOX channels add slack"
  set +e
  env "${env_args[@]}" nemohermes "$SANDBOX" channels add slack
  rc=$?
  set -e
fi

unset SLACK_BOT_TOKEN SLACK_APP_TOKEN

# ---------------------------------------------------------------------------------------
step "Done (nemohermes exit code $rc)"
if [ "$rc" -ne 0 ]; then
  warn "A nonzero exit code does not always mean failure. Check the real state with the verify script below."
fi
cat <<EOF
  Next, run the read-only checks:
    $SCRIPT_DIR/verify-slack.sh

  Then, optionally, one test post to #claimtrace:
    $SCRIPT_DIR/verify-slack.sh --post <channel_id>
EOF
exit "$rc"
