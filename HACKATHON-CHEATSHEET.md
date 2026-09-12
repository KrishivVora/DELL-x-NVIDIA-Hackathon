# Dell x NVIDIA Hackathon (Cornell) — Cheatsheet

## Event
- **Sat Sep 12, 2026** · doors 9:00 AM · winners ~9:00 PM ET · eHub Collegetown, 409 College Ave
- Discord: https://discord.gg/F9KDnE67z · BuilderBase dashboard (email + OTP) — confirm your roster there
- Teams of 2–4, one **Dell Pro Max with GB10** per team (40 teams max)
- **Rules:** no pre-built agents (plans, scaffolds, libraries are OK). Required stack: **NemoClaw + OpenClaw + OpenShell**. All inference local on the GB10 — **no cloud LLM calls**. IP stays with you.
- **Submission (every team):** repo + video demo + pitch deck. **Top 8** pitch live: 5 min (3 min demo + 2 min Q&A).

## Judging
| Criterion | Weight | What they want |
|---|---|---|
| Local-first + always-on | 30% | Runs fully on the GB10, no cloud LLM. The agent **acts on its own over time**. |
| Business value | 30% | A real corporate workflow with measurable impact — something a company would pay for. |
| Demo + pitch | 30% | Clear 5-minute pitch: what you built and why it matters. |
| Technical execution | 10% | Working end-to-end demo, correct use of NemoClaw + OpenClaw + OpenShell, doesn't break. |

## How the stack fits together
- **OpenShell** — sandbox runtime for agents: gateway (control plane), sandbox, policy engine (filesystem / network / process rules in YAML), inference router.
- **OpenClaw** — the agent itself: always-on assistant with a web dashboard and `openclaw tui`.
- **NemoClaw** — NVIDIA's installer + reference stack: installs OpenShell, creates an OpenClaw sandbox, wires it to local vLLM, manages policy, channels (Telegram/Slack/Discord), logs.
- **GB10** = ARM64 Grace CPU + Blackwell GPU, 128 GB unified memory, DGX OS. Anything you copy to it must be **arm64**.
- The "Hermes" playbook from the email uses `nvidia/Qwen3.6-35B-A3B-NVFP4` — the **same model** NemoClaw's express install picks on the GB10. Keep OpenClaw as the agent (it's required).

## On the GB10 (demo box)
**Where to run these:** in a terminal *on the GB10 itself*, not in your laptop's WSL. Either sit at the box (DGX OS desktop → open Terminal) or SSH in from your laptop with `ssh <user>@<gb10-ip>`. Get the login from the organizers; `hostname -I` on the box shows its IP. Run the long install at the box's own terminal so a dropped SSH connection can't kill it.

Check what's already there first — organizers may have pre-provisioned it:
```bash
head -n 2 /etc/os-release; nvidia-smi; docker info --format '{{.ServerVersion}}'
command -v nemoclaw openshell && nemoclaw list
```

Install + onboard (express install is the recommended path):
```bash
curl -fsSL https://www.nvidia.com/nemoclaw.sh | bash
# Press Enter at "Run express install with these settings? [Y/n]"
#   model qwen3.6-35b-a3b-nvfp4 · sandbox my-assistant · managed local vLLM · policy Balanced
#   First run downloads ~40–80 GB (model + containers) — start this EARLY.
source ~/.bashrc && nemoclaw --version
```

Use it:
```bash
nemoclaw my-assistant dashboard-url --quiet   # -> http://127.0.0.1:18790/#token=...
# From your laptop:  ssh -L 18790:127.0.0.1:18790 <user>@<gb10-ip>   then open that URL
nemoclaw my-assistant connect                 # shell into the sandbox, then:  openclaw tui
nemoclaw my-assistant status
nemoclaw my-assistant logs --follow
nemoclaw list
openshell term                                # live monitoring TUI
curl http://127.0.0.1:8000/v1/models          # is local vLLM up?
```

Gotchas:
- Use **`127.0.0.1`, not `localhost`** — otherwise "origin not allowed".
- Port 18789/18790 busy → `lsof -i :18789` and kill that PID.
- Gateway cgroup errors → add `"default-cgroupns-mode": "host"` to `/etc/docker/daemon.json`, `sudo systemctl restart docker`, or re-run the installer.
- CoreDNS crash loop → re-run the installer.
- Docker permission denied → `sudo usermod -aG docker $USER`, then log out/in.
- Installing over SSH without a TTY → `NEMOCLAW_NON_INTERACTIVE=1 NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1` (this accepts the third-party license).
- First model response can take a while; k3s startup logs are noisy but not errors.

## Offline bundle — `gb10-offline-bundle/` (arm64, checksum-verified)
| Folder | Contents |
|---|---|
| `NemoClaw/` | NemoClaw source @ `lkg` (commit f75f722, 2026-09-11) — git submodule, so clone this repo with `--recursive` |
| `openshell-v0.0.106-arm64/` | OpenShell 0.0.106 CLI + gateway + sandbox tarballs and `.deb` — the exact version NemoClaw `lkg` pins |
| `nodejs-arm64/` | Node.js 22.23.2 and 24.21.0 linux-arm64 tarballs |

**Not included:** model weights and container images (~40–80 GB). Those still need network on the GB10 — ask organizers whether the boxes are pre-provisioned.

Use only if the box is missing these and Wi-Fi is slow:
```bash
# Easiest — on the GB10, clone this repo (bundle included):
git clone --recursive https://github.com/KrishivVora/DELL-x-NVIDIA-Hackathon.git ~/hackathon
cd ~/hackathon   # the bundle is at ~/hackathon/gb10-offline-bundle

# Or from laptop PowerShell, in this folder:
scp -r gb10-offline-bundle <user>@<gb10-ip>:~/

# On the GB10:
cd ~/gb10-offline-bundle/openshell-v0.0.106-arm64
for f in openshell-*.tar.gz; do tar xzf "$f"; done
sudo install -m 755 openshell openshell-gateway openshell-sandbox /usr/local/bin/
openshell --version   # NemoClaw's installer sees a pinned-compatible OpenShell and skips downloading it

# Only if Node is missing or older than 22.19:
sudo tar -xJf ~/gb10-offline-bundle/nodejs-arm64/node-v22.23.2-linux-arm64.tar.xz -C /usr/local --strip-components=1
```

## Laptop (this machine)
- WSL Ubuntu has the CLIs (verified): Node v24.21.0 (nvm) · OpenShell 0.0.106 (`~/.local/bin`) · NemoClaw v0.0.123 (`~/NemoClaw`, lkg) · OpenClaw 2026.7.1 · Docker 29.7.2.
- Installed by `setup/laptop-wsl-setup.sh` — no sudo, re-runnable; teammates on WSL/Linux x86_64 can use it too. Log: `~/hackathon-laptop-setup.log` in WSL.
- Docker Desktop must be running (WSL integration is on for `Ubuntu`). It does **not** auto-start — open it after a reboot.
- NemoClaw's contributor "doctor" shows failures for GitHub CLI, hadolint, git signing, etc. — those only matter for contributing to NemoClaw, not using it. It also flags WSL Docker at 7.4 GiB (< 8 GiB); only relevant if you build sandboxes on the laptop.
- **Not onboarded on purpose:** no NVIDIA GPU here. Onboard and run inference on the GB10; write code/skills on the laptop and work on the box over SSH (VS Code Remote-SSH works well).

## Links
- NemoClaw DGX Spark playbook: https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/nemoclaw (also https://build.nvidia.com/spark/nemoclaw)
- NemoClaw: https://github.com/NVIDIA/NemoClaw · docs https://docs.nvidia.com/nemoclaw/
- OpenShell: https://github.com/NVIDIA/OpenShell · docs https://docs.nvidia.com/openshell/
- OpenClaw: https://github.com/openclaw/openclaw
- Hermes agent playbook: https://build.nvidia.com/spark/hermes-agent
- GB10 install gotchas write-up (older versions, still useful): https://ai-muninn.com/en/blog/nemoclaw-install-gx10-from-scratch
