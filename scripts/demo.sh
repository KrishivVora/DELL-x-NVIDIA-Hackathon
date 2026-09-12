#!/usr/bin/env bash
# Rehearsed demo path. Run on the GB10 with the sandbox live.
#   ./scripts/demo.sh my-assistant project-123
set -euo pipefail
SANDBOX="${1:-my-assistant}"
PROJECT="${2:-project-123}"

step() { printf '\n\033[1;36m>>> %s\033[0m\n' "$*"; read -r -p "enter to run: "; }

step "1/4  Show the audit is local: model, route, no cloud provider"
nemoclaw "$SANDBOX" status

step "2/4  Full audit of $PROJECT (one non-interactive Hermes turn)"
nemoclaw "$SANDBOX" exec -- hermes -z "Audit project $PROJECT" -s claimtrace --yolo

step "3/4  The local report"
nemoclaw "$SANDBOX" exec -- cat "/sandbox/workspace/projects/$PROJECT/reports/latest.md"

step "4/4  Replace the result file and say nothing — the watcher re-audits"
echo "swap the CSV in another terminal, then watch the watcher log"
