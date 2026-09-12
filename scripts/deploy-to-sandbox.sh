#!/usr/bin/env bash
# Deploy the ClaimTrace agent layer into a running Hermes sandbox.
#
# Run on the GB10 from the repo root:
#     ./scripts/deploy-to-sandbox.sh my-assistant
#
# Re-runnable. Does not recreate or destroy anything.
set -euo pipefail

SANDBOX="${1:-my-assistant}"
APP_DIR="/sandbox/workspace/claimtrace-app"
PROJECTS_DIR="${CLAIMTRACE_SANDBOX_PROJECTS:-/sandbox/workspace/projects}"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "Checking sandbox '$SANDBOX'"
nemoclaw "$SANDBOX" status

say "Creating directories in the sandbox"
nemoclaw "$SANDBOX" exec -- mkdir -p "$APP_DIR" "$PROJECTS_DIR"

say "Uploading the claimtrace package"
nemoclaw "$SANDBOX" upload ./claimtrace/ "$APP_DIR/claimtrace/"
nemoclaw "$SANDBOX" upload ./bin/claimtrace /usr/local/bin/claimtrace
nemoclaw "$SANDBOX" exec -- chmod +x /usr/local/bin/claimtrace

say "Ensuring pandas is present"
nemoclaw "$SANDBOX" exec -- python3 -c 'import pandas; print("pandas", pandas.__version__)' \
  || nemoclaw "$SANDBOX" exec -- pip install --no-input pandas

say "Installing the claimtrace skill"
nemoclaw "$SANDBOX" skill install ./skills/claimtrace/
nemoclaw "$SANDBOX" skill list | grep -i claimtrace || echo "WARNING: skill not listed; start a new Hermes session and re-check"

say "Smoke test inside the sandbox"
nemoclaw "$SANDBOX" exec --workdir "$APP_DIR" -- env \
  PYTHONPATH="$APP_DIR" CLAIMTRACE_PROJECTS_ROOT="$PROJECTS_DIR" \
  python3 -m claimtrace --project __none__ project-status \
  || echo "(expected: 'project not found' — the tool surface is reachable)"

cat <<NOTE

Deployed.
  app:      $APP_DIR
  projects: $PROJECTS_DIR
  skill:    /sandbox/.hermes/skills/claimtrace

Next:
  1. Put a project under $PROJECTS_DIR/<project-id>/ (ingestion lane, or
     'nemoclaw $SANDBOX upload ./projects/<id>/ $PROJECTS_DIR/<id>/').
  2. One audit turn:
       nemoclaw $SANDBOX exec -- hermes -z "Audit project <id>" -s claimtrace --yolo
  3. Always-on watcher:
       nemoclaw $SANDBOX exec -- env PYTHONPATH=$APP_DIR CLAIMTRACE_PROJECTS_ROOT=$PROJECTS_DIR \\
         python3 -m claimtrace.watcher --project <id> --interval 5
NOTE
