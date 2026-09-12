# Runtime / Slack lane

Everything for the "Runtime / Slack" lane in `CONTRACT.md`: getting the Hermes
sandbox (`my-hermes`, managed by NemoClaw) to talk to Slack, and the host-side
helper that posts the sanitized `labmate notify` line into `#claimtrace`.

State on the GB10 as of 2026-09-12, ~4:15 PM ET: Slack is connected. The bot
(`@ClaimTrace`) is installed in the team workspace, the private `#claimtrace`
channel exists, `verify-slack.sh` passes every check, and the sandbox has
`/home/dell/claimtrace-data/projects` mounted read-only at `/sandbox/projects`.

## Order of operations (what was done, and what to redo after a recreate)

1. **Create the Slack app** at https://api.slack.com/apps → *From a manifest*,
   pasting `slack-app/manifest-agent-view.json` (or `manifest-classic.json` if
   Slack rejects Agent view). Generate an app-level token (`xapp-…`, scope
   `connections:write`), install the app (`xoxb-…` bot token), create the private
   channel, `/invite @ClaimTrace`, collect the channel ID and member IDs.
   Tokens never go in this repo; NemoClaw stores them in OpenShell's credential
   store and the sandbox only sees placeholders.
2. **Read-only project mount** (once): `scripts/connect-slack.sh` in its default
   mode recreates the sandbox with `--host-mount …:/sandbox/projects`.
   Gotcha found the hard way: recreating an existing sandbox *skips* the
   messaging picker (NemoClaw keeps the registry's "no channels" decision), so
   Slack cannot be added in the same run. Hence step 3.
3. **Connect Slack**: `scripts/connect-slack.sh --channels-add`
   (= `nemohermes my-hermes channels add slack` + rebuild). The mount survives a
   rebuild. The Hermes API bearer token changes on every rebuild — anything that
   calls `127.0.0.1:8642` must fetch it at startup with
   `nemohermes my-hermes gateway-token --quiet`, never store it.
4. **Verify**: `scripts/verify-slack.sh`, then
   `scripts/verify-slack.sh --post <channel_id>` to post one test message.
   Manual: DM the bot "ping" (allowlisted account), @mention it in the channel,
   confirm a non-allowlisted account gets nothing.
5. **Slack behaviour settings**:
   `scripts/apply-hermes-slack-config.sh --channel <C…> --skill claimtrace --apply`
   (dry-run without `--apply`). Written with host-side `nemohermes config set`,
   which NemoClaw replays on rebuild but **not** on recreate. Most important key:
   `display.platforms.slack.tool_progress: off` — NemoClaw's global default would
   otherwise post every file path and command the agent runs into the channel.
   Run it after the skill is installed, or drop `--skill` and re-run later.
   Research and evidence: `docs/hermes-slack-config.md`.

   One of the keys it writes is global rather than Slack-only:
   `tools.tool_search.enabled: false`. NemoClaw builds sandboxes with
   *progressive tool disclosure*, so each session sees only part of the tool
   catalog; when `terminal` is not visible the model wraps CLI calls in
   `execute_code` ("from hermes_tools import terminal"), which raises a script
   approval on every step and makes it look like the agent is writing Python to
   read files. Session exports showed 13 `terminal` calls in one session and 5
   `execute_code` calls with zero `tool_search` calls in the next. With the key
   off, a verified API turn used `terminal` directly. The durable equivalent,
   which survives a sandbox recreate, is
   `nemohermes my-hermes rebuild --tool-disclosure direct`.

## Sending from the box: `worker/slack_notify.py`

Hermes 0.20.6 gives the agent no `send_message` tool, so unprompted posts
(alerts, digests) are sent by a host process through the sandbox:

    nemohermes my-hermes exec -- hermes send --to slack:<C…>[:<thread_ts>] <text>

`SlackNotifier.send(...)` wraps that with a timeout, one retry, ANSI/token
redaction, and de-duplication via a `notifications` collection (duck-typed;
pass `None` to skip). The intended input is the one line `labmate notify
--project <id>` prints (CONTRACT.md §6). The formatting helpers in the module
predate that contract and render more than the contract allows unless called
with `metadata_only=True`; prefer passing the `labmate notify` line straight
through. Tests: `cd runtime && python3 -m unittest discover -s tests` (36 tests,
stdlib only, nothing is sent).

## `skill-drafts/`

`slack-replies.md` (reply rules, templates, prompt-injection handling) and
`answer-mode-section.md` (an `answer` mode for `skills/claimtrace/SKILL.md`)
were written against the earlier plan's worker HTTP API on
`host.openshell.internal:8700`. The contract has since moved that to the
`labmate` CLI inside the sandbox. The rules and templates still apply; the
"which call to make" parts need mapping to `labmate` commands by the skill
owner before they go into `skills/`. They deliberately live here, not in
`skills/`, until then.
