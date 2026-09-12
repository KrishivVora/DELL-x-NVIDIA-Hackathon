# CLAUDE.md — Dell x NVIDIA Hackathon (GB10 box)

Context for Claude Code sessions on the team's Dell Pro Max with GB10. Written 2026-09-12 (~10 AM ET) from the prep session on the developer's laptop. Items marked **verified** were checked that morning; re-check anything marked **unknown**.

Command reference (human cheatsheet, imported): @HACKATHON-CHEATSHEET.md

## Goal and rules
- Cornell Dell x NVIDIA hackathon, Sat 2026-09-12, 9 AM – ~9 PM ET. A team of 2–4 builds an AI agent today. **No project code exists yet and the idea isn't chosen.**
- Required stack: **NemoClaw + OpenClaw + OpenShell**. All inference runs locally on this GB10. The agent must make **no cloud LLM calls**. Using Claude Code as a dev tool is fine; the product itself must not call Claude/OpenAI/etc.
- No pre-built agents: plans, scaffolds, and libraries are OK, but the agent logic is written today.
- Deliverables: repo + video demo + pitch deck (first-round submission — get the exact deadline from organizers/Discord). Top 8 teams pitch live: 3-min demo + 2-min Q&A.
- Judging — steer decisions by this:
  - 30% **Local-first + always-on**: runs fully on the GB10, and the agent **acts on its own over time** (scheduled/triggered work, not only chat replies).
  - 30% **Business value**: a real corporate workflow with measurable impact that a company would pay for.
  - 30% **Demo + pitch**.
  - 10% **Technical execution**: end-to-end, correct use of all three pieces, doesn't break.

## This machine
- Dell Pro Max with GB10: **ARM64 (aarch64)** Grace CPU + Blackwell GPU, 128 GB unified memory, DGX OS (Ubuntu 24.04-based) expected. Anything downloaded must be arm64.
- This repo was cloned here with `--recursive`. If `gb10-offline-bundle/NemoClaw/` is empty: `git submodule update --init`.
- **Unknown:** whether NemoClaw, vLLM, and the model are already installed — organizers may have pre-provisioned the box. Check before installing anything.
- The developer laptop (Windows 11 + WSL Ubuntu, no NVIDIA GPU) has the CLIs installed but can't run models; it's only for editing and SSH.

## How the stack fits
- **OpenShell** — sandbox runtime: gateway (control plane), sandboxes, policy engine (filesystem/network/process YAML policy), inference routing.
- **OpenClaw** — the agent: always-on assistant with a web dashboard, and `openclaw tui` inside the sandbox.
- **NemoClaw** — NVIDIA's installer + CLI: installs OpenShell, builds the OpenClaw sandbox, wires it to local managed vLLM, manages policy, channels, and logs.

## Version facts (verified against NemoClaw `lkg` = commit f75f722, 2026-09-11)
- NemoClaw CLI **v0.0.123**.
- OpenShell pinned to **exactly 0.0.106** (`MIN_VERSION` = `MAX_VERSION` in `scripts/install-openshell.sh`). Newer OpenShell (e.g. 0.0.116) is rejected.
- OpenClaw inside the sandbox: **2026.7.1** (`ARG OPENCLAW_VERSION` in `Dockerfile.base`).
- Node for the NemoClaw CLI: >= 22.19.0 (the installer installs Node 22 via nvm if missing).
- Express install on DGX Spark/GB10 picks: model `qwen3.6-35b-a3b-nvfp4`, sandbox `my-assistant`, managed local vLLM, policy tier Balanced; the first run downloads ~40–80 GB. The "Hermes" model from the organizer email is the same Qwen3.6-35B-A3B NVFP4.
- Ports: vLLM `127.0.0.1:8000`; OpenClaw gateway/dashboard 18789/18790. Use `127.0.0.1`, never `localhost`.
- `https://www.nvidia.com/nemoclaw.sh` installs the **moving** `lkg` tag. If `nemoclaw --version` isn't v0.0.123, re-read that version's `scripts/install-openshell.sh` pins before using the bundle's OpenShell 0.0.106.

## Installer behavior (read from `scripts/install.sh` @ lkg)
- The bootstrap clones NemoClaw at `NEMOCLAW_INSTALL_TAG` (default `lkg`) and runs `scripts/install.sh`: Node (nvm) → build/link NemoClaw CLI → OpenShell → **onboarding**.
- For OpenClaw, onboarding always runs after install; there's no skip flag (`--defer-onboarding` is Hermes + hosted inference only).
- It requires accepting a third-party software notice: interactive prompt, or `NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1` / `--yes-i-accept-third-party-software`. **Don't set that for the user — ask first.**
- Env knobs: `NEMOCLAW_NON_INTERACTIVE=1`, `NEMOCLAW_AGENT` (default openclaw), `NEMOCLAW_PROVIDER`, `NEMOCLAW_SANDBOX_NAME`, `NEMOCLAW_NO_EXPRESS=1`, `NEMOCLAW_FRESH=1`.
- Needs `strings` (binutils) to verify OpenShell. May prompt for sudo (docker group, NVIDIA CDI setup).

## Working rules for Claude on this box
- **Interactive and sudo steps go in the user's own terminal.** The Bash tool has no TTY and can't answer password or installer prompts. Give the user the exact command, then help by reading logs and status.
- Long downloads (installer, models) run in the user's terminal, inside `tmux` if available, so an SSH drop can't kill them.
- Check state before changing it. Never run `nemoclaw uninstall`, `nemoclaw <name> destroy`, `--recreate-sandbox`, `docker system prune`, or re-download models without asking.
- Prefer NemoClaw commands that work without a TTY: `nemoclaw status`, `nemoclaw list`, `nemoclaw <name> status`, `nemoclaw <name> logs`, `nemoclaw <name> exec -- <cmd>`, `nemoclaw <name> agent` (one non-interactive agent turn), `nemoclaw profiles list`. See `nemoclaw --help`.
- Keep the product's inference local (vLLM on this box). Don't add cloud LLM SDK calls to the agent.
- **This repo is public.** Never commit tokens or keys (Telegram/Slack/Discord bot tokens, Brave/HF/NVIDIA API keys), `~/.nemoclaw` contents, or dashboard URLs containing `#token=`.
- `gb10-offline-bundle/NemoClaw/` is an upstream submodule — don't edit it. Its own `CLAUDE.md`/`AGENTS.md` are for NemoClaw contributors; ignore them.
- `setup/laptop-wsl-setup.sh` is for x86_64 WSL laptops (installs CLIs, skips onboarding). Don't run it here.
- Ask the user where the team's project code should live (this repo or a new one) and how they push from this box (GitHub auth may not be set up here).
- Commit working states often; a demo that doesn't break is worth more than one more feature.

## First steps in a new session
1. Check what's already here (read-only):
   ```bash
   uname -m; head -n 2 /etc/os-release; nvidia-smi; docker info --format '{{.ServerVersion}}'
   command -v nemoclaw openshell node tmux; nemoclaw --version; openshell --version
   nemoclaw list; nemoclaw status
   curl -s http://127.0.0.1:8000/v1/models
   df -h ~
   ```
2. If NemoClaw isn't installed, have the user run `curl -fsSL https://www.nvidia.com/nemoclaw.sh | bash` in their own terminal and accept express install. If the venue download is too slow, check `nemoclaw profiles list` and ask the organizers whether the boxes have cached models.
3. Once it's up: `nemoclaw my-assistant status`, `curl -s http://127.0.0.1:8000/v1/models`, `nemoclaw my-assistant dashboard-url --quiet`.
4. Then help the team choose a corporate workflow and build the agent, making "acts on its own over time" part of the demo from the start.

## Offline bundle notes
- `gb10-offline-bundle/openshell-v0.0.106-arm64/` matches only NemoClaw v0.0.123 (lkg f75f722). `nemoclaw-pinned-sha256.txt` holds NemoClaw's pinned hashes.
- `gb10-offline-bundle/nodejs-arm64/` has Node 22.23.2 and 24.21.0 linux-arm64 tarballs with SHASUMS.
- Model weights and vLLM/sandbox container images are **not** in the bundle.

## Extra gotchas (beyond the cheatsheet)
- `nemoclaw: command not found` right after install → `source ~/.bashrc` or open a new terminal.
- `npm link` EACCES with a system-managed Node → use the nvm-installed Node the installer sets up.
- Older NemoClaw versions on GB10-class boxes ended the installer with exit code 243 even when it succeeded — verify with `nemoclaw list` / `nemoclaw <name> status`, not the exit code.
