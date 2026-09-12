# Labmate — project status

**As of Saturday 2026-09-12, 4:40 PM ET** (the box clock is UTC: 20:40).
Winners are announced around 9 PM. Get the submission deadline from the organizers/Discord.

Labmate is an always-on, local research agent for a lab's unpublished work. Hermes Agent
runs inside an NVIDIA OpenShell sandbox managed by NemoClaw, on a Qwen3.6-35B model served
locally on the Dell GB10. Researchers talk to it in Slack; it answers questions, prepares
meeting briefings, and checks manuscript claims against the project's own data. MongoDB
holds the ingested documents and retrieval index. Nothing but short, sanitized Slack messages
leaves the machine. The interface between the three lanes is frozen in `CONTRACT.md`.

## 1. What is running on the GB10 right now (verified)

| Piece | State |
|---|---|
| NemoClaw v0.0.123 + OpenShell 0.0.106 | installed; CLI is `nemohermes` (needs `source ~/.bashrc` in old shells) |
| Sandbox `my-hermes` | Ready, Hermes Agent v0.20.6, GPU passthrough verified |
| Local model | `nvidia/Qwen3.6-35B-A3B-NVFP4` on NemoClaw-managed vLLM, `127.0.0.1:8000`; no cloud inference configured |
| Hermes API / dashboard | `http://127.0.0.1:8642/v1` (bearer token: `nemohermes my-hermes gateway-token --quiet`) / `http://127.0.0.1:18789/` |
| Read-only project mount | `/home/dell/claimtrace-data/projects` → `/sandbox/projects` (ro); projects `demo-001` (manuscript.pdf, results.csv, config.yaml, extracted/, manifest.json) and `discrete-math` |
| Slack | **connected** over Socket Mode: bot `@ClaimTrace`, private `#claimtrace`, 4 allowlisted members; `runtime/scripts/verify-slack.sh` passes every check |
| MongoDB | Atlas Local 8.0.28 in container `hackathon-mongodb`, `127.0.0.1:27017`, single-node replica set; db `claimtrace` has `documents` (5) and `chunks` (61) for demo-001 |
| RAG service | `python -m labmate_rag serve --bind auto --port 8700` running on the host (from the `feat/pdf-rag-box` checkout) |
| Agent layer in the sandbox | `/sandbox/workspace/labmate-app` (labmate, labmate_rag, vendored pandas) and skills `claimtrace`, `meeting-prep`, `research-assistant` are present |
| Sandbox network policy | Balanced tier + `brew, huggingface, local-inference, npm, pypi, slack` |

Known gaps in the running state (see §4):
- The `/usr/local/bin/labmate` wrapper is **missing** inside the sandbox — the Slack rebuild replaced the image. Re-run `scripts/deploy-to-sandbox.sh my-hermes`.
- The sandbox **cannot reach the RAG service on 8700 yet** (policy denies it). The preset `policy/labmate-rag-host.yaml` exists only on `feat/pdf-rag-box`.
- No scheduler or watcher process is running, and there are no Hermes cron jobs, so nothing is acting on its own yet.
- Slack behaviour settings (`runtime/scripts/apply-hermes-slack-config.sh --apply`) are not applied. Until they are, the agent's tool calls (file paths, commands) would be posted into Slack.

## 2. What is on GitHub

**On `main` (PRs #1–#5 merged):**
- Plan documents (`CLAIMTRACE-PROJECT-PLAN*.md`; now superseded by `CONTRACT.md`).
- `CONTRACT.md`, `labmate/` (cli, store, verify, retrieval, watcher, meetings, report, **scheduler**), `bin/labmate`, `skills/` (claimtrace, meeting-prep, research-assistant), `scripts/deploy-to-sandbox.sh`, `scripts/demo.sh`, `tests/smoke.sh`, `demo-data/`.
- `runtime/` — the Slack lane: connect/verify/config scripts, `docs/hermes-slack-config.md`, `worker/slack_notify.py` (+36 tests), Slack app manifests, skill drafts.

**On GitHub but not on `main`:**
- `feat/pdf-rag-box` (RAG lane, 7 commits ahead, last one just pushed): `labmate_rag/` package (extract, ingest, embeddings, db, retrieval, api, cli, watch), `policy/labmate-rag-host.yaml`, `scripts/make-demo-manuscript.py`, `tests/test_rag.py`, `tests/test_state_root.py`, requirements. **Conflicts with `main` in `scripts/deploy-to-sandbox.sh`** — the RAG owner should rebase/merge and open the PR.
- `docs/claimtrace-plan-v2` (2 commits): an older revision of the plan document. Superseded; safe to close.
- `feat/pdf-RAG`: fully contained in `feat/pdf-rag-box`; safe to delete.

**Local only:** nothing that belongs in the repo. The checkout on the box is on `feat/pdf-rag-box` and is clean. (`claimtrace-slack/` in the checkout is a git-ignored duplicate of `runtime/`; delete it.)

## 3. Lane status

| Lane (CONTRACT.md) | Done | Not done |
|---|---|---|
| Ingestion / RAG | extraction, manifest, MongoDB store + chunks, `retrieve()` API served on 8700, demo-001 ingested | merge to main; let the sandbox reach 8700 (apply the policy preset); prove `labmate retrieve` inside the sandbox uses the MongoDB backend, not the keyword fallback |
| Agent / skills | `labmate` CLI (ask, digest, meeting-brief, verify, notify), skills, watcher, scheduler with rate limits, smoke test | redeploy after the rebuild; Slack `answer` behaviour (`runtime/skill-drafts/` → `labmate` commands); run the scheduler + watcher as the always-on loop; one full rehearsed audit |
| Runtime / Slack | NemoClaw + OpenShell install, local vLLM, read-only mount, Slack connected, scripts + notifier | test post + DM/@mention test; apply Slack config; RAG policy preset; wire `labmate notify` → Slack; tighten policy before the demo |

## 4. What's left, in order

1. **Re-deploy the agent layer** into the rebuilt sandbox: `./scripts/deploy-to-sandbox.sh my-hermes` (restores `/usr/local/bin/labmate`). Verify with `nemohermes my-hermes exec -- labmate --help`.
2. **Finish Slack** (runtime lane):
   `runtime/scripts/verify-slack.sh --post <channel_id>` → DM the bot "ping" → `@ClaimTrace ping` in the channel → non-allowlisted account gets nothing →
   `runtime/scripts/apply-hermes-slack-config.sh --channel <channel_id> --skill claimtrace --apply`.
3. **Open the sandbox → RAG service path**: merge `feat/pdf-rag-box`, then `nemohermes my-hermes policy add --from-file policy/labmate-rag-host.yaml` and `nemohermes my-hermes exec -- curl -s http://host.openshell.internal:8700/...`.
4. **Always-on**: start `python -m labmate.scheduler --project demo-001 --interval 60` and the watcher inside the sandbox (or as Hermes cron jobs) and leave them running for the rest of the afternoon so the demo shows real unattended activity. Post `labmate notify` output to `#claimtrace` through `runtime/worker/slack_notify.py` (`metadata_only=True`) or `hermes send`.
5. **End-to-end rehearsal** (`scripts/demo.sh`, `tests/smoke.sh`): full audit on demo-001 → swap `results.csv` → claim flips Supported → Conflicting → Slack alert with no prompt → ask "@ClaimTrace why …" in Slack. Run it five times; time each model call and write the numbers down for the pitch.
6. **Tighten before the demo**: remove `npm`, `pypi`, `huggingface`, `brew` presets (`nemohermes my-hermes policy remove …`), keep `local-inference`, `slack`, and the RAG preset; re-run the rehearsal.
7. **Submission**: a real `README.md` (the current one is a single line), demo video, pitch deck (problem → local architecture → live demo → business model/impact). Confirm the deadline and the "one of three tools" + MongoDB rules on Discord in writing.
8. **Housekeeping**: `CLAUDE.md` and `HACKATHON-CHEATSHEET.md` still describe the old OpenClaw/all-three-tools plan — update or mark them historical; drop the two superseded plan documents; run `git credential-cache exit` before returning the box.

## 5. Operational notes (things that bit us today)

- **The Hermes API bearer token changes on every sandbox rebuild.** Fetch it at startup; never store it.
- **`nemohermes onboard --recreate-sandbox` skips the messaging picker** for an existing sandbox, and `--recreate-sandbox` also wipes keys set with `nemohermes config set`. Order: recreate (mount) → `channels add slack` → config → deploy/skills/cron. A plain `rebuild` keeps the mount and replays config keys.
- **Plain `nemohermes onboard` defaults to NVIDIA cloud inference** in its provider menu. Always choose "Local vLLM (localhost:8000)" or set `NEMOCLAW_PROVIDER=vllm`.
- **Docker access is a per-user socket ACL** (`setfacl`), lost if Docker restarts; reapply or reboot once (the group membership then covers it). Don't restart Docker, vLLM, or the sandbox right before the demo — each costs minutes.
- The model runs at most 4 requests at once; keep teammates from chatting with the bot during the live demo.
- The host install in `~/.hermes` is a teammate's earlier Hermes, configured for a cloud provider. It is not part of the product; never point anything at it.
- Slack is the one cloud path: only the `labmate notify` line (ids, counts, paths) may leave the box.
