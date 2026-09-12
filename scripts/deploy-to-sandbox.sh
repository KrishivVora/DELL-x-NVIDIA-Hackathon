#!/usr/bin/env bash
# Deploy the Labmate agent layer (+ the labmate_rag client) into a running
# Hermes sandbox.
#
# Run on the GB10 from the repo root:
#     ./scripts/deploy-to-sandbox.sh my-hermes
#
# Re-runnable. Does not recreate or destroy anything.
#
# Verified on the GB10 (NemoClaw v0.0.123, Hermes 0.20.6):
#   - `upload DIR/ DEST/` nests DIR inside DEST, so upload into the parent.
#   - /usr/local/bin is read-only in the sandbox; the wrapper goes to
#     /sandbox/.local/bin, which is added to PATH in /sandbox/.bashrc.
#   - retrieval runs on the host (labmate_rag serve --bind auto); the sandbox
#     reaches it at http://host.openshell.internal:8700 through the
#     labmate-rag-host policy preset.
set -euo pipefail

SANDBOX="${1:-my-hermes}"
APP_DIR="/sandbox/workspace/labmate-app"
PROJECTS_DIR="${LABMATE_SANDBOX_PROJECTS:-/sandbox/workspace/projects}"
BIN_DIR="/sandbox/.local/bin"
SKILLS="research-assistant meeting-prep claimtrace"
NC="${NEMOCLAW_CLI:-nemoclaw}"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "Checking sandbox '$SANDBOX'"
"$NC" "$SANDBOX" status

say "Creating directories in the sandbox"
"$NC" "$SANDBOX" exec -- mkdir -p "$APP_DIR" "$PROJECTS_DIR" "$BIN_DIR"

say "Uploading the labmate and labmate_rag packages"
"$NC" "$SANDBOX" exec -- rm -rf "$APP_DIR/labmate" "$APP_DIR/labmate_rag"
"$NC" "$SANDBOX" upload ./labmate/ "$APP_DIR/"
"$NC" "$SANDBOX" upload ./labmate_rag/ "$APP_DIR/"
"$NC" "$SANDBOX" upload ./bin/labmate "$BIN_DIR/labmate"
"$NC" "$SANDBOX" exec -- chmod +x "$BIN_DIR/labmate"
"$NC" "$SANDBOX" exec -- sh -c "grep -q '$BIN_DIR' /sandbox/.bashrc || echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> /sandbox/.bashrc"

say "Ensuring pandas is present"
"$NC" "$SANDBOX" exec -- python3 -c 'import pandas; print("pandas", pandas.__version__)' \
  || "$NC" "$SANDBOX" exec -- pip install --no-input pandas

say "Allowing the sandbox to reach the host retrieval API (policy preset)"
"$NC" "$SANDBOX" policy add --from-file ./policy/labmate-rag-host.yaml --yes

say "Installing the skills"
for skill in $SKILLS; do
  "$NC" "$SANDBOX" skill install "./skills/$skill/"
done
"$NC" "$SANDBOX" skill list || echo "WARNING: could not list skills; start a new Hermes session and re-check"

say "Smoke test inside the sandbox"
"$NC" "$SANDBOX" exec --workdir "$APP_DIR" -- env \
  PYTHONPATH="$APP_DIR" LABMATE_PROJECTS_ROOT="$PROJECTS_DIR" \
  python3 -m labmate --project __none__ project-status \
  || echo "(expected: 'project not found' — the tool surface is reachable)"
"$NC" "$SANDBOX" exec -- curl -sf -m 5 http://host.openshell.internal:8700/api/health \
  || echo "WARNING: host retrieval API not reachable. On the host run:  .venv/bin/python -m labmate_rag serve --bind auto"

cat <<NOTE

Deployed.
  app:      $APP_DIR
  wrapper:  $BIN_DIR/labmate  (on PATH for new shells)
  projects: $PROJECTS_DIR
  skills:   /sandbox/.hermes/skills/{research-assistant,meeting-prep,claimtrace}
  retrieval: labmate_rag -> http://host.openshell.internal:8700 (host: labmate_rag serve --bind auto)

Next:
  1. On the host, ingest a project (writes manifest.json, extracted/, and the index):
       .venv/bin/python -m labmate_rag ingest <project-id>
     then copy it into the sandbox (or use a read-only host mount):
       $NC $SANDBOX upload ./projects/<project-id>/ $PROJECTS_DIR/
  2. Ask it something:
       $NC $SANDBOX exec -- hermes -z "What is in project <id>?" -s research-assistant --yolo
  3. Prep a meeting:
       $NC $SANDBOX exec -- hermes -z "Prep me for my next meeting on <id>" -s meeting-prep --yolo
  4. Always-on watcher:
       $NC $SANDBOX exec -- env PYTHONPATH=$APP_DIR LABMATE_PROJECTS_ROOT=$PROJECTS_DIR \\
         python3 -m labmate.watcher --project <id> --interval 5
NOTE
