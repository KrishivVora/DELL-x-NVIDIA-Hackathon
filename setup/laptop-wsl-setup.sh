#!/usr/bin/env bash
# Dell x NVIDIA Hackathon - laptop dev setup (WSL2 Ubuntu, x86_64).
#
# Installs the required stack's CLIs without sudo and WITHOUT onboarding:
#   - Node.js 24 via nvm (NemoClaw needs >=22.19, OpenClaw 2026.7.1 needs >=24.15 on the 24 line)
#   - OpenShell, at the exact version NemoClaw pins, into ~/.local/bin
#   - NemoClaw CLI, built from the last-known-good (lkg) release
#   - OpenClaw CLI, same version NemoClaw bakes into its sandbox image
#
# Onboarding (sandbox + local model) is intentionally skipped: this laptop has no
# NVIDIA GPU. Run `nemoclaw onboard` on the GB10 box instead.
#
# Prereq: Docker Desktop running with WSL integration enabled for this distro.
# Usage:  bash laptop-wsl-setup.sh      (safe to re-run)

set -o pipefail

NEMOCLAW_REF="lkg"
NVM_VERSION="v0.40.4"
NVM_SHA256="4b7412c49960c7d31e8df72da90c1fb5b8cccb419ac99537b737028d497aba4f" # pinned by NemoClaw's installer
NODE_MAJOR="24"
OPENCLAW_VERSION="2026.7.1" # OPENCLAW_VERSION default in NemoClaw lkg Dockerfile.base
NEMOCLAW_DIR="$HOME/NemoClaw"
LOG="${LOG:-$HOME/hackathon-laptop-setup.log}"

exec > >(tee -a "$LOG") 2>&1
step() { printf '\n==== %s ====\n' "$*"; }
die() {
  printf 'ERROR: %s\n' "$*"
  exit 1
}

step "1/6 nvm ${NVM_VERSION} + Node.js ${NODE_MAJOR}"
export NVM_DIR="$HOME/.nvm"
if [ ! -s "$NVM_DIR/nvm.sh" ]; then
  tmp="$(mktemp)"
  curl -fsSL "https://raw.githubusercontent.com/nvm-sh/nvm/${NVM_VERSION}/install.sh" -o "$tmp" || die "nvm download failed"
  echo "${NVM_SHA256}  ${tmp}" | sha256sum -c - || die "nvm installer checksum mismatch"
  bash "$tmp" || die "nvm install failed"
  rm -f "$tmp"
fi
# shellcheck disable=SC1091
. "$NVM_DIR/nvm.sh"
nvm install "$NODE_MAJOR" --no-progress || die "nvm install $NODE_MAJOR failed"
nvm alias default "$NODE_MAJOR" >/dev/null
nvm use default >/dev/null
echo "node $(node --version), npm $(npm --version)"

step "2/6 NemoClaw source (${NEMOCLAW_REF})"
if [ ! -d "$NEMOCLAW_DIR/.git" ]; then
  git clone --depth 1 --branch "$NEMOCLAW_REF" https://github.com/NVIDIA/NemoClaw.git "$NEMOCLAW_DIR" || die "clone failed"
fi
git -C "$NEMOCLAW_DIR" log -1 --format='NemoClaw commit: %H (%cd)'

step "3/6 OpenShell (NemoClaw-pinned version, user-local install)"
mkdir -p "$HOME/.local/bin"
export PATH="$HOME/.local/bin:$PATH"
(cd "$NEMOCLAW_DIR" && NEMOCLAW_NON_INTERACTIVE=1 bash scripts/install-openshell.sh </dev/null) || die "OpenShell install failed"
openshell --version

step "4/6 PATH for new shells"
path_line='export PATH="$HOME/.local/bin:$PATH"'
grep -qxF "$path_line" "$HOME/.bashrc" || printf '\n# openshell / nemoclaw CLIs\n%s\n' "$path_line" >>"$HOME/.bashrc"
echo "~/.local/bin is on PATH in ~/.bashrc"

step "5/6 NemoClaw CLI (dev-setup --expose-cli: npm install, build, link; no onboarding)"
if ! (cd "$NEMOCLAW_DIR" && NODE_OPTIONS=--max-old-space-size=5120 ./scripts/dev-setup.sh --expose-cli); then
  echo "WARN: dev-setup exited non-zero (often contributor-only doctor checks); verifying CLI below"
fi
hash -r

step "6/6 OpenClaw CLI ${OPENCLAW_VERSION}"
npm install -g "openclaw@${OPENCLAW_VERSION}" || die "openclaw install failed"

step "Summary"
printf '%-10s %s\n' "node" "$(node --version 2>&1)"
printf '%-10s %s\n' "npm" "$(npm --version 2>&1)"
printf '%-10s %s\n' "docker" "$(docker version --format '{{.Server.Version}}' 2>&1 | head -1)"
printf '%-10s %s\n' "openshell" "$(openshell --version 2>&1 | head -1)"
printf '%-10s %s\n' "nemoclaw" "$( (command -v nemoclaw >/dev/null && nemoclaw --version) 2>&1 | head -1)"
printf '%-10s %s\n' "openclaw" "$(openclaw --version 2>&1 | head -1)"
echo "Log: $LOG"
