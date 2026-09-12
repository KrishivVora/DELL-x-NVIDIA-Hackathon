#!/usr/bin/env bash
# Deploy the Labmate agent layer into a running Hermes sandbox.
#
# Run on the GB10 from the repo root:
#     ./scripts/deploy-to-sandbox.sh my-assistant
#
# Re-runnable. Does not recreate or destroy anything.
set -euo pipefail

SANDBOX="${1:-my-assistant}"
APP_DIR="/sandbox/workspace/labmate-app"
PROJECTS_DIR="${LABMATE_SANDBOX_PROJECTS:-/sandbox/workspace/projects}"
SKILLS="research-assistant meeting-prep claimtrace"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "Checking sandbox '$SANDBOX'"
nemoclaw "$SANDBOX" status

say "Creating directories in the sandbox"
nemoclaw "$SANDBOX" exec -- mkdir -p "$APP_DIR" "$PROJECTS_DIR"

say "Uploading the labmate package"
nemoclaw "$SANDBOX" upload ./labmate/ "$APP_DIR/labmate/"
nemoclaw "$SANDBOX" upload ./bin/labmate /usr/local/bin/labmate
nemoclaw "$SANDBOX" exec -- chmod +x /usr/local/bin/labmate

say "Ensuring pandas is present"
nemoclaw "$SANDBOX" exec -- python3 -c 'import pandas; print("pandas", pandas.__version__)' \
  || nemoclaw "$SANDBOX" exec -- pip install --no-input pandas

say "Installing the skills"
for skill in $SKILLS; do
  nemoclaw "$SANDBOX" skill install "./skills/$skill/"
done
nemoclaw "$SANDBOX" skill list || echo "WARNING: could not list skills; start a new Hermes session and re-check"

say "Smoke test inside the sandbox"
nemoclaw "$SANDBOX" exec --workdir "$APP_DIR" -- env \
  PYTHONPATH="$APP_DIR" LABMATE_PROJECTS_ROOT="$PROJECTS_DIR" \
  python3 -m labmate --project __none__ project-status \
  || echo "(expected: 'project not found' — the tool surface is reachable)"

cat <<NOTE

Deployed.
  app:      $APP_DIR
  projects: $PROJECTS_DIR
  skills:   /sandbox/.hermes/skills/{research-assistant,meeting-prep,claimtrace}

Next:
  1. Put a project under $PROJECTS_DIR/<project-id>/ (ingestion lane, or
     'nemoclaw $SANDBOX upload ./projects/<id>/ $PROJECTS_DIR/<id>/').
  2. Ask it something:
       nemoclaw $SANDBOX exec -- hermes -z "What is in project <id>?" -s research-assistant --yolo
  3. Prep a meeting:
       nemoclaw $SANDBOX exec -- hermes -z "Prep me for my next meeting on <id>" -s meeting-prep --yolo
  4. Always-on watcher:
       nemoclaw $SANDBOX exec -- env PYTHONPATH=$APP_DIR LABMATE_PROJECTS_ROOT=$PROJECTS_DIR \\
         python3 -m labmate.watcher --project <id> --interval 5
NOTE
