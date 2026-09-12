#!/usr/bin/env bash
# Archive every Claude Code file on this box (plus the irreplaceable demo state)
# into the repo, redact secrets, commit, and push. Written 2026-09-12 because the
# team loses access to the GB10 after the hackathon.
#
# Run it YOURSELF in a terminal on the box (Claude's own shell is blocked from
# reading ~/.claude):
#     bash scripts/archive-claude-files.sh
#
# What it collects, into claude-archive/ :
#   home-dot-claude/   ~/.claude  (session transcripts, memory, settings, todos ...)
#                      EXCLUDED: .credentials.json, shell-snapshots/ (env dumps),
#                      statsig/, cache dirs, debug logs, lock files
#   scratchpad/        /tmp/claude-1000/*/*  (subagent transcripts, task outputs)
#   repo-dot-claude/   .claude/ dirs and settings inside this repo (not the submodule)
#   hermes-sessions/   every Hermes session exported from the sandbox as Markdown
#   sandbox-state/     /sandbox/.hermes/workspace/labmate-state (claims, reports)
#   host-project-data/ ~/claimtrace-data/projects (demo-001, discrete-math)
#
# Every text file is passed through a redaction pass (Slack/GitHub/HF/NVIDIA/
# OpenAI tokens, JWTs, MongoDB URIs with passwords, Bearer tokens, dashboard
# #token= URLs, private keys, gmail addresses). Files over 15 MB are gzipped.
# A final scan lists anything that still looks like a secret and asks before pushing.
#
# THE REPO IS PUBLIC. Transcripts contain everything every teammate typed on
# this box. If you would rather keep the archive private:
#     ARCHIVE_REMOTE=https://github.com/<you>/<private-repo>.git bash scripts/archive-claude-files.sh
# (pushes a branch "claude-archive" there instead of main here).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE="$REPO/claude-archive"
SANDBOX="${SANDBOX:-my-hermes}"
export PATH="$HOME/.nvm/versions/node/v22.23.2/bin:$HOME/.local/bin:$PATH"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "Collecting into $ARCHIVE"
rm -rf "$ARCHIVE"
mkdir -p "$ARCHIVE"

# 1. ~/.claude
if [ -d "$HOME/.claude" ]; then
  mkdir -p "$ARCHIVE/home-dot-claude"
  rsync -a \
    --exclude='.credentials.json' --exclude='*credentials*' \
    --exclude='shell-snapshots/' --exclude='statsig/' --exclude='cache/' --exclude='*cache*/' \
    --exclude='debug/' --exclude='*.lock' --exclude='node_modules/' --exclude='versions/' \
    "$HOME/.claude/" "$ARCHIVE/home-dot-claude/"
  echo "  ~/.claude -> home-dot-claude ($(du -sh "$ARCHIVE/home-dot-claude" | cut -f1))"
fi

# 2. scratchpad + task outputs (subagent transcripts)
for d in /tmp/claude-1000/*/*/; do
  [ -d "$d" ] || continue
  dest="$ARCHIVE/scratchpad/$(basename "$(dirname "$d")")/$(basename "$d")"
  mkdir -p "$dest"
  rsync -a --exclude='wt-*/' --exclude='*.venv/' --exclude='node_modules/' "$d" "$dest/"
done
[ -d "$ARCHIVE/scratchpad" ] && echo "  scratchpad -> $(du -sh "$ARCHIVE/scratchpad" | cut -f1)"

# 3. repo-local Claude settings (outside the NemoClaw submodule)
find "$REPO" -path "$REPO/gb10-offline-bundle" -prune -o -path "$ARCHIVE" -prune -o \
  \( -name '.claude' -type d -o -name '.claude.json' -o -name 'settings.local.json' \) -print 2>/dev/null |
  while read -r p; do
    rel="${p#"$REPO"/}"; mkdir -p "$ARCHIVE/repo-dot-claude/$(dirname "$rel")"
    cp -a "$p" "$ARCHIVE/repo-dot-claude/$rel"
  done
[ -f "$HOME/.claude.json" ] && echo "  (skipped ~/.claude.json: holds account/auth details)"

# 4. Hermes sessions + sandbox state (best effort; needs the sandbox up)
if command -v nemohermes >/dev/null 2>&1 && nemohermes "$SANDBOX" status >/dev/null 2>&1; then
  say "Exporting Hermes sessions and Labmate state from sandbox $SANDBOX"
  nemohermes "$SANDBOX" exec --timeout 300 -- sh -c \
    'rm -rf /sandbox/workspace/_archive && mkdir -p /sandbox/workspace/_archive/hermes-sessions && hermes sessions export --format md --yes /sandbox/workspace/_archive/hermes-sessions >/dev/null 2>&1; cp -a /sandbox/.hermes/workspace/labmate-state /sandbox/workspace/_archive/sandbox-state 2>/dev/null; ls /sandbox/workspace/_archive' \
    2>/dev/null | grep -v 'Active gateway' || true
  nemohermes "$SANDBOX" download /sandbox/workspace/_archive "$ARCHIVE/" >/dev/null 2>&1 \
    && { mv "$ARCHIVE/_archive/"* "$ARCHIVE/" 2>/dev/null; rmdir "$ARCHIVE/_archive" 2>/dev/null; echo "  hermes-sessions + sandbox-state downloaded"; } \
    || echo "  WARNING: download from the sandbox failed; run: nemohermes $SANDBOX download /sandbox/workspace/_archive $ARCHIVE/"
else
  echo "  (sandbox not reachable; skipping Hermes sessions and sandbox state)"
fi

# 5. host demo project data (the folder is named to dodge the 'projects/' gitignore rule)
if [ -d "$HOME/claimtrace-data/projects" ]; then
  mkdir -p "$ARCHIVE/host-project-data"
  rsync -a "$HOME/claimtrace-data/projects/" "$ARCHIVE/host-project-data/"
  echo "  host-project-data -> $(du -sh "$ARCHIVE/host-project-data" | cut -f1)"
fi

say "Redacting secrets"
python3 - "$ARCHIVE" <<'PY'
import os, re, sys, gzip, shutil
root = sys.argv[1]
PATTERNS = [
    (re.compile(r'xox[abprs]-[A-Za-z0-9-]{8,}'), '[REDACTED_SLACK_TOKEN]'),
    (re.compile(r'xapp-[A-Za-z0-9-]{8,}'), '[REDACTED_SLACK_APP_TOKEN]'),
    (re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})'), '[REDACTED_GITHUB_TOKEN]'),
    (re.compile(r'\bhf_[A-Za-z0-9]{20,}'), '[REDACTED_HF_TOKEN]'),
    (re.compile(r'\bnvapi-[A-Za-z0-9_-]{20,}'), '[REDACTED_NVIDIA_KEY]'),
    (re.compile(r'\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}'), '[REDACTED_API_KEY]'),
    (re.compile(r'\btvly-[A-Za-z0-9_-]{16,}'), '[REDACTED_TAVILY_KEY]'),
    (re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'), '[REDACTED_JWT]'),
    (re.compile(r'(mongodb(?:\+srv)?://)[^:/\s@"\']+:[^@\s"\']+@'), r'\1[REDACTED]@'),
    (re.compile(r'((?:MONGODB_INITDB_ROOT_PASSWORD|MONGODB_URI|API_SERVER_KEY|SLACK_BOT_TOKEN|SLACK_APP_TOKEN|HF_TOKEN|HUGGING_FACE_HUB_TOKEN|NVIDIA_API_KEY|NVIDIA_INFERENCE_API_KEY|TAVILY_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|OPENROUTER_API_KEY|GITHUB_TOKEN|GH_TOKEN)\s*[=:]\s*)["\']?[^\s"\']{6,}'), r'\1[REDACTED]'),
    (re.compile(r'(Bearer\s+)[A-Za-z0-9._~+/=-]{20,}'), r'\1[REDACTED]'),
    (re.compile(r'(#token=)[A-Za-z0-9._-]+'), r'\1[REDACTED]'),
    (re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----', re.S), '[REDACTED_PRIVATE_KEY]'),
    (re.compile(r'[A-Za-z0-9._%+-]+@gmail\.com'), '[redacted-email]'),
]
TEXT_EXT = {'.jsonl', '.json', '.md', '.txt', '.yaml', '.yml', '.log', '.sh', '.py', '.csv', '.env', '.html', '.toml', '.ini', '.cfg', ''}
BIG = 15 * 1024 * 1024
total_hits, files_touched, gz = 0, 0, 0
for dp, dn, fn in os.walk(root):
    for f in fn:
        p = os.path.join(dp, f)
        if os.path.islink(p): continue
        ext = os.path.splitext(f)[1].lower()
        if ext not in TEXT_EXT and not f.startswith('.'): continue
        try:
            with open(p, 'rb') as fh: raw = fh.read()
        except OSError: continue
        if b'\x00' in raw[:4096]: continue
        try: s = raw.decode('utf-8')
        except UnicodeDecodeError: continue
        hits = 0
        for rx, rep in PATTERNS:
            s, n = rx.subn(rep, s); hits += n
        if hits:
            total_hits += hits; files_touched += 1
            with open(p, 'w', encoding='utf-8') as fh: fh.write(s)
        if os.path.getsize(p) > BIG:
            with open(p, 'rb') as fi, gzip.open(p + '.gz', 'wb') as fo: shutil.copyfileobj(fi, fo)
            os.remove(p); gz += 1
print(f"  redacted {total_hits} secret-like strings in {files_touched} files; gzipped {gz} files over 15 MB")
PY

say "Residual scan (should be empty)"
grep -rIlE 'xox[abprs]-|xapp-|gh[pousr]_[A-Za-z0-9]{20}|github_pat_|\bhf_[A-Za-z0-9]{20}|nvapi-|mongodb://[^ ]*:[^ ]*@|BEGIN [A-Z ]*PRIVATE KEY' "$ARCHIVE" 2>/dev/null | head -20 | tee /tmp/claude-archive-residual.txt || true
if [ -s /tmp/claude-archive-residual.txt ]; then
  printf '\nSome files above still match a secret pattern (may be gzipped or false positives). Inspect them, then re-run, or continue at your own risk.\nContinue anyway? [y/N]: '
  read -r a; case "$a" in y|Y) ;; *) echo "Stopped; nothing committed."; exit 1;; esac
fi

# Never commit anything larger than GitHub's hard limit.
find "$ARCHIVE" -type f -size +95M -print -delete | sed 's/^/  dropped (>95 MB): /' || true

cat > "$ARCHIVE/README.md" <<'MD'
# Claude archive

Everything Claude Code produced on the hackathon GB10 (shared `dell` account, all
teammates' sessions), captured before the box was returned. Secrets were
redacted with `scripts/archive-claude-files.sh`; files over 15 MB are gzipped.

- `home-dot-claude/projects/<dir>/*.jsonl` — full session transcripts (one JSON object per line).
- `home-dot-claude/projects/<dir>/memory/` — Claude's persistent memory notes: the fastest way to
  re-learn what was set up and why.
- `scratchpad/` — subagent transcripts and task outputs.
- `hermes-sessions/` — every Hermes Agent session from the sandbox (Slack conversations included), as Markdown.
- `sandbox-state/` — Labmate's claim records, meetings and reports from the sandbox.
- `host-project-data/` — the demo projects (originals, extracted text, manifests).
MD

say "Committing"
cd "$REPO"
git add scripts/archive-claude-files.sh "$ARCHIVE"
git commit -q -m "chore: archive Claude Code files, Hermes sessions and demo state from the GB10

Secrets redacted; see claude-archive/README.md.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>" || { echo "nothing to commit"; exit 0; }
echo "  $(git log -1 --format='%h %s')  ($(du -sh "$ARCHIVE" | cut -f1))"

if [ -n "${ARCHIVE_REMOTE:-}" ]; then
  say "Pushing branch claude-archive to $ARCHIVE_REMOTE"
  git push "$ARCHIVE_REMOTE" HEAD:claude-archive
else
  say "Pushing to origin main"
  git push origin HEAD:main
fi
echo "Done."
