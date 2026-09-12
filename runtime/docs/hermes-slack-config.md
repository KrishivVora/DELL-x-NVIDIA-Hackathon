# Hermes Slack Settings for ClaimTrace

Checked on 2026-09-12 against:

- **Sandbox `my-hermes`:** Hermes Agent v0.20.6 (2026.8.27), with source in `/opt/hermes`.
- **NemoClaw v0.0.123 source:** `gb10-offline-bundle/NemoClaw` (lkg f75f722).

Slack isn't connected yet, so nothing here has been tested against a live workspace. Line numbers refer to those exact versions. Paths starting with `/opt/hermes` are inside the sandbox (`nemohermes my-hermes exec -- sed -n '<a>,<b>p' <path>`). Other code paths are relative to `gb10-offline-bundle/NemoClaw/`.

Script: `runtime/scripts/apply-hermes-slack-config.sh --channel C0... --skill claimtrace`. Without `--apply` it only prints the plan.

## Summary

- **Write Slack settings from the host with `nemohermes my-hermes config set`, then run `nemohermes my-hermes gateway restart`.**
  - `rebuild` replays every key written this way.
  - Edits made with `hermes config set` inside the sandbox work until the next rebuild, then disappear.
- **`onboard --recreate-sandbox` and `destroy` lose these settings.** Run the script again afterwards.
- **You must override `tool_progress` for Slack.**
  - NemoClaw's generated config sets a global `display.tool_progress: all`, and Hermes ranks a global value above Slack's built-in `off`.
  - Without a Slack override, each tool call posts a permanent line into the channel with a 40-character preview of its arguments (file paths, curl commands). That breaks the sanitized-message rule in plan §16.5.
- **Everything on the candidate list is supported in 0.20.6 except two items:**
  - `reply_to_mode` has no effect on Slack.
  - There is no per-platform reasoning effort.

## 1. Settings Supported in 0.20.6

The Slack adapter is a bundled plugin at `/opt/hermes/plugins/platforms/slack/adapter.py`, not `gateway/platforms/slack.py`.

- **Where options live:** the adapter reads them from `self.config.extra`, which is `platforms.slack.extra` in `~/.hermes/config.yaml`.
- **Partial copying:** `gateway/config.py:1691-1754` copies only some keys from `platforms.slack.<key>` into `extra`.
- **What to use:** put every adapter option under `platforms.slack.extra.<key>`.
- **Top-level `slack:` block:** the v0.21.2 docs show one, but I didn't trace that path in 0.20.6, so don't use it.

| Setting | In 0.20.6? | Key to use (env fallback) | Default | Evidence |
|---|---|---|---|---|
| require_mention | Yes | `platforms.slack.extra.require_mention` (`SLACK_REQUIRE_MENTION`) | `true` | adapter.py:9043-9060, gate 6468-6471 |
| strict_mention | Yes | `platforms.slack.extra.strict_mention` (`SLACK_STRICT_MENTION`) | `false` | adapter.py:9062-9075, 6492 |
| reply_in_thread | Yes | `platforms.slack.extra.reply_in_thread` | `true` | adapter.py:3874, 6375; config.py:1703 |
| reply_to_mode | **No effect on Slack** | `platforms.slack.reply_to_mode` is parsed, but nothing Slack-side reads it | `first` | Parsed at config.py:661, 757. Only Telegram and Discord read it (config.py:2010, 2049). Neither the Slack adapter nor `gateway/platforms/base.py` reads it. |
| unauthorized_dm_behavior | Yes | `platforms.slack.extra.unauthorized_dm_behavior`: `pair` or `ignore` (the global top-level key also works) | `pair` | config.py:1018, 1346-1362, 1691; run.py:17162-17172 |
| allowed_channels | Yes | `platforms.slack.extra.allowed_channels` (`SLACK_ALLOWED_CHANNELS`, which NemoClaw writes to `.env`) | empty, meaning any channel | adapter.py:9186-9201, 6441 |
| channel_skill_bindings | Yes | `platforms.slack.extra.channel_skill_bindings: [{id, skills: [...]}]` | none | base.py:2899-2950; adapter.py:7069; run.py:19653-19675 (new sessions only) |
| channel_prompts | Yes | `platforms.slack.extra.channel_prompts: {<channel id>: text}` | none | base.py:2869-2896; adapter.py:7053 |
| suggested_prompts | Yes | `platforms.slack.extra.suggested_prompts`: a list of `{title, message}`, or `{title, prompts}`; at most 4 | `[]` | adapter.py:5248-5279 |
| display.live_status per platform | Yes | `display.platforms.slack.live_status`: `full`, `verb`, or `off` | `full` | display_config.py:62-70, 288-298; run.py:28788 |
| typing_status_text | Yes | `platforms.slack.typing_status_text`, or the same key under `extra` | `is thinking...` | config.py:686, 739-743; adapter.py:3643-3662 |
| group_sessions_per_user | Yes, global only | top-level `group_sessions_per_user` | `true` | config.py:974, 1464-1467 |
| rich_blocks | Yes | `platforms.slack.extra.rich_blocks` | `false`, but **NemoClaw renders `true`** | adapter.py:4148-4161; `src/lib/messaging/channels/slack/manifest.ts:136-150` |
| tool_progress per platform | Yes | `display.platforms.slack.tool_progress`: `off`, `new`, `all`, `verbose`, or `log` | Slack's built-in default is `off`, **but NemoClaw's global `all` overrides it** | display_config.py:143-148 and 187 (precedence: per-platform, then global, then platform default); run.py:28712-28731; `agents/hermes/config/managed-policy.ts:233-236` |
| allow_bots | Yes | `platforms.slack.extra.allow_bots`: `none`, `mentions`, or `all` (`SLACK_ALLOW_BOTS`) | `none` | adapter.py:3822-3832, 6123-6131 |
| reaction_triggers | Yes | `platforms.slack.extra.reaction_triggers`: a bool or a list of emoji names (`SLACK_REACTION_TRIGGERS`) | off | adapter.py:5709-5738 |
| agent.reasoning_effort | Yes, global | `agent.reasoning_effort`; per model, `agent.reasoning_overrides` | Not set in `my-hermes`, so the provider default applies | hermes_constants.py:1487-1545; run.py:9487-9560 |
| Per-platform reasoning effort | **No** | Only per model, per session (`/reasoning`), or per cron job (`hermes cron create --reasoning-effort`) | none | Same evidence as the row above. `display.platforms.slack.show_reasoning` only shows or hides reasoning text. |

`display.platforms.slack.interim_assistant_messages` also matters (run.py:28800-28810). NemoClaw sets it to `true` globally, so Slack receives mid-turn commentary unless you override it.

## 2. How NemoClaw Manages `config.yaml` and `.env`

### Files are generated at image build and stay writable

- **Generation:** the image build writes `/sandbox/.hermes/config.yaml` and `.env` (`agents/hermes/Dockerfile:972-985`, `agents/hermes/generate-config.ts:4-9`, `agents/hermes/config/write-config.ts:18-42`).
- **Slack render:** the Slack channel manifest adds `platforms.slack: {enabled: true, extra: {rich_blocks: true}}`, plus `SLACK_ALLOWED_USERS` and `SLACK_ALLOWED_CHANNELS` in `.env` (`manifest.ts:126-150`).
- **Permissions:** both files are `sandbox:sandbox 0640`, so they aren't read-only. A root-owned hash, `/etc/nemoclaw/hermes.config-hash` (mode 0444), serves as the trust anchor.
- **Direct edits don't block restarts:** startup and `gateway restart` check the secret boundary, file paths, and a stable snapshot. Then they use whatever valid config is on disk (`docs/manage-sandboxes/runtime-controls.mdx:75-80, 98`).

### `hermes config set` inside the sandbox is lost on rebuild

- A rebuild creates a new image with a freshly generated `config.yaml`.
- Snapshots deliberately exclude `config.yaml` and `.env` (`docs/manage-sandboxes/backup-restore.mdx:207`, `workspace-files.mdx:248`, `messaging-channels.mdx:74`).
- The Hermes manifest's `state_dirs` and `state_files` don't include `config.yaml` (`agents/hermes/manifest.yaml:70-137`).

### `nemohermes <name> config set` on the host survives rebuilds

When you run `config set`:

1. It validates the whole candidate with Hermes' own `GatewayConfig.from_dict` (`agents/hermes/runtime-config-guard.py:2842-2870`).
2. It writes the file in a sealed transaction.
3. It appends `config set hermes:<dotpath>` to `operational-audit.jsonl` in NemoClaw's host state directory (`src/lib/sandbox/config.ts:1405-1413`, `src/lib/state/audit/operational.ts:18-19`).

When you later run `rebuild`:

1. It reads those dotpaths from the audit log.
2. Before deleting the sandbox, it captures their live values.
3. After state restore, it deep-merges those values into the new config.
4. It verifies them and prints `Hermes operator config restore: restored=...; dropped=...`.

Evidence: `src/lib/actions/sandbox/rebuild-pipeline.ts:465-488`; `rebuild-durable-config.ts:407-476, 551-623`; `rebuild-restore-phase.ts:39-95`; `docs/manage-sandboxes/recover-rebuild-sandboxes.mdx:326-330`.

Rebuild skips four kinds of keys (`rebuild-durable-config.ts:305-320, 398-405, 437-439`):

- `gateway.*`
- dotpaths with a numeric segment
- managed model and provider route fields
- values that contain credential material

The rebuild-time Slack render merges into existing objects (`src/lib/messaging/applier/agent-config.ts:516-522`), so replayed `platforms.slack.extra.*` keys aren't wiped.

### What survives each operation

| Operation | Keys written with `nemohermes config set` | Edits made with `hermes config set` in the sandbox |
|---|---|---|
| `gateway restart`, `stop` / `start` | Kept | Kept |
| `rebuild`, `upgrade-sandboxes`, and the rebuild that `channels add/remove/stop/start` triggers (`src/lib/actions/sandbox/policy-channel.ts:1095`) | Replayed | Lost |
| `onboard --recreate-sandbox`, or `destroy` followed by onboard | **Lost.** Only the rebuild pipeline captures them (`rebuild-pipeline.ts:473`) | Lost |

### Other ways to persist config, and why not

- **NemoClaw config overlay:** none exists for Hermes in v0.0.123.
- **`onboard --from <Dockerfile>`:** builds a custom image, which is too heavy for today.
- **Adding entries to `state_files` or `state_dirs`:** those live in the upstream submodule (don't edit it), and they exclude `config.yaml` on purpose.
- **Re-applying after every rebuild:** unnecessary, because rebuild replays the keys. Re-apply only after recreate or destroy. The script skips keys that already match, so running it again is safe.

### `config set` gotchas

- **New keys need a flag without a TTY:** a key that doesn't exist yet is refused unless you pass `--config-accept-new-path` (`src/lib/sandbox/config.ts:1311-1342`). Every ClaimTrace key is new.
- **Private URLs are rejected:** URLs inside values are validated, and private URLs are refused unless `security.allow_private_urls: true` (`config.ts:1344-1359`, `docs/reference/commands.mdx:1354`). Keep `http://host.openshell.internal:8700` out of the channel prompt; the skill carries it.
- **Only `config.yaml` changes:** to change the Slack allowlists in `.env`, re-run `nemohermes my-hermes channels add slack`, which rebuilds. Don't edit `.env` inside the sandbox.

## 3. Making Hermes Pick Up Changes

- **Restart is required:**
  - Adapter options under `platforms.slack.extra` are fixed when the gateway starts.
  - `display.*` appears to be re-read for each message (run.py:28675-28688), but still restart after any change.
- **Verified command:** `nemohermes my-hermes gateway restart [--quiet]`.
  - It appears in `nemohermes --help` and `nemohermes my-hermes gateway restart --help`.
  - Implementation: `src/commands/sandbox/gateway/restart.ts:32-38`. NemoClaw documents it for reloading Hermes config (`recover-rebuild-sandboxes.mdx:141-146`).
  - Alternative: add `--restart` to the last `config set` (`src/commands/sandbox/config/set.ts:38-40`).
- **Don't use `hermes gateway restart` inside the sandbox:** it manages a systemd or launchd service (`hermes gateway --help`), but NemoClaw's supervisor runs `hermes gateway run` directly (`agents/hermes/manifest.yaml:25`).
- **Side effects:**
  - A restart interrupts turns in progress but keeps the API token.
  - A rebuild issues a new token (`docs/reference/commands.mdx:1960`, `recover-rebuild-sandboxes.mdx:337`).
  - Don't restart right before the demo (plan §18 rule 20).

## Recommended Settings

| Key | Value | Why (plan section) |
|---|---|---|
| `platforms.slack.extra.require_mention` | `true` | Channel chatter can't use up the 4 model request slots (§18 rules 4 and 17). It's already the default; pinning it records the intent and gets replayed on rebuild. |
| `platforms.slack.extra.strict_mention` | leave unset (`false`) | Follow-up questions in a thread work without another @mention. |
| `platforms.slack.extra.reply_in_thread` | `true` | Answers go under the question, while alerts and digests stay top-level, so `#claimtrace` reads as a timeline (§17.4 step 5). |
| `platforms.slack.extra.unauthorized_dm_behavior` | `"ignore"` | A non-allowlisted account gets no reply instead of a pairing code (§16.3 step 4, §19). |
| `platforms.slack.extra.allow_bots` | `"none"` | Posts by other apps or bots, including the worker's `hermes send` alerts, never reach the agent as prompts (§16.6). |
| `platforms.slack.extra.channel_skill_bindings` | `[{"id":"<C...>","skills":["claimtrace"]}]`, plus DM IDs | Auto-loads the skill (§15.5). This happens only when a session starts. There's no wildcard for DMs: each person's DM with the bot has its own `D...` ID. Install the skill first; otherwise Hermes logs "Auto-skill not found". |
| `platforms.slack.extra.channel_prompts` | `{"<C...>": "<short prompt>"}`, same for DM IDs | Repeats the sanitized-reply rules on every turn (§15.5, §16.5). The text is fixed, so prefix caching still works (§18 rule 6). No URLs. |
| `platforms.slack.extra.suggested_prompts` | title "ClaimTrace", with prompts for demo-001 status, why claim-003 is conflicting, re-audit demo-001, and what needs attention | One-click demo questions (§8, §16.1). Slack shows them only in the app's assistant DM pane. |
| `display.platforms.slack.live_status` | `"verb"` | The status line says "is reading..." without file paths or commands (§16.5, §19). |
| `display.platforms.slack.tool_progress` | `"off"` | **Required.** Without it, NemoClaw's global `all` posts tool calls with argument previews into Slack (§16.5). |
| `display.platforms.slack.interim_assistant_messages` | `false` | Mid-turn commentary could quote file contents before the final sanitized reply (§16.5). |
| `platforms.slack.extra.rich_blocks` | keep `true`; don't set it | NemoClaw's Slack manifest owns this key and renders it again on each rebuild. |
| `allowed_channels` | don't set; use `SLACK_ALLOWED_CHANNELS` | The adapter prefers the config key over the environment variable, so setting both creates two sources of truth. |
| `group_sessions_per_user` | leave `true` | One teammate's context doesn't leak into another teammate's answers (§18 rule 11). |
| `reaction_triggers`, `typing_status_text` | leave at defaults | ClaimTrace doesn't need them, and reaction triggers would require more Slack scopes. |
| `agent.reasoning_effort` | **leave unchanged** (team decision, §18 rule 5) | The setting is global: it covers Slack, API runs, and cron together. For the sweep job, use `hermes cron create --reasoning-effort` instead. |

### Unsupported Items and Fallbacks

- **`reply_to_mode` on Slack:** use `reply_in_thread` to control threading.
- **Per-platform reasoning effort:** use the global `agent.reasoning_effort`, the per-job cron flag, or `/reasoning` for a single session (untested from Slack).
- **Binding every DM to a skill:** pass known DM IDs with `--dm`, or rely on the channel prompt plus Hermes' skill index.

## What to Redo After a Rebuild or Recreate

- **After `rebuild` (including rebuilds started by `channels ...` commands):**
  1. Nothing needs re-applying. Check the rebuild output for `Hermes operator config restore: restored=...` with no ClaimTrace keys under `dropped`.
  2. Run the script without `--apply`; it should print "Nothing to change".
  3. Have the worker fetch the new API token: `nemohermes my-hermes gateway-token --quiet`.
- **After `onboard --recreate-sandbox`, or `destroy` followed by onboard:** these settings are gone.
  1. Install the skill: `nemohermes my-hermes skill install ./skills/claimtrace/`.
  2. Run the script with `--apply`.
  3. Recreate the policy preset and cron job (§16.4, §17.3).
- **If the Slack channel changes:** update `SLACK_ALLOWED_CHANNELS` with `channels add slack`, then re-run the script with the new `--channel`.
- **After any `hermes config set` inside the sandbox:** that edit disappears on the next rebuild unless the same key was earlier written with `nemohermes config set`.

## Not Yet Verified

- **Slack behavior end to end.** Slack isn't configured yet. After connecting it, confirm:
  - A DM "ping" gets a reply.
  - An @mention in the channel gets a reply in a thread.
  - A DM from a non-allowlisted account gets no reply.
  - No tool-progress lines appear in Slack.
  - The status line shows only verbs.
- **Rebuild replay of these keys.** I read the replay code but haven't run a rebuild. Check the restored and dropped lists. In particular, I didn't find the credential filter's key rules, so I can't confirm the prompt and binding values pass it.
- **Recreate losing the keys.** This comes from reading the code (`onboard` never calls the capture step); I haven't observed it.
- **Suggested prompts and the live status line.** Both need the Slack app's assistant features and the `assistant:write` scope. Our app manifest hasn't been checked.
- **Whether `nemohermes config export` includes values written with `config set`.** Not checked.
