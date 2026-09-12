<!--
For the SKILL.md owner: paste everything below this comment under "## Procedure" in skills/claimtrace/SKILL.md.
Checked against Hermes 0.20.6 in the my-hermes sandbox: skill_view(name, file_path) loading, Slack session context, terminal approval patterns, and the proxy's 403 policy_denied response.
The worker API is planned, not built. Paths and body fields follow CLAIMTRACE-PROJECT-PLAN.md §16.4.
-->

### `answer` mode: Slack messages

**When to use**
- A Slack DM or @mention: the Current Session Context says **Source:** Slack, or the input says `answer` mode.
- Never for `extract_claims`, `map_evidence`, `explain`, or `sweep` runs. Those return JSON, not Slack text.

**Steps**
1. Load the reply rules: `skill_view(name="claimtrace", file_path="references/slack-replies.md")`. Follow them for the whole reply.
2. Pick the request type with the decision procedure in that file. If the message isn't about ClaimTrace, reply with its template F and stop without running any tool.
3. Take the project ID and claim ID from the message. No project ID: run **List projects** first.
4. Run the matching command below with the `terminal` tool. For a "why" question, run **Claims**, then **Events**. Copy the command exactly and change only the IDs.
5. Check the last output line against **Reading the result**.
6. If the worker is unreachable:
   - Question: read `/sandbox/projects/<project_id>/state/claims.json` with `read_file` and answer from it, with the staleness line.
   - Audit request: don't read the snapshot and don't retry. Reply that nothing was queued.
7. Write one reply with the matching template from `references/slack-replies.md`.
8. Stop. Never send a second POST for the same message.

**Commands.** `host.openshell.internal` is the GB10 host. Keep each command on one line.

Health:
```bash
/usr/bin/curl -sS --connect-timeout 3 --max-time 8 -w '\nHTTP %{http_code}\n' http://host.openshell.internal:8700/api/health
```

List projects:
```bash
/usr/bin/curl -sS --connect-timeout 3 --max-time 8 -w '\nHTTP %{http_code}\n' http://host.openshell.internal:8700/api/projects
```

Claims (replace `demo-001`):
```bash
/usr/bin/curl -sS --connect-timeout 3 --max-time 8 -w '\nHTTP %{http_code}\n' http://host.openshell.internal:8700/api/projects/demo-001/claims
```

Events (replace `demo-001`). Add `?since=2026-09-12T14:00:00Z` only when the user or the API gave that exact time; otherwise leave it off:
```bash
/usr/bin/curl -sS --connect-timeout 3 --max-time 8 -w '\nHTTP %{http_code}\n' http://host.openshell.internal:8700/api/projects/demo-001/events
```

Audit request (POST once; replace every value in the JSON):
```bash
/usr/bin/curl -sS --connect-timeout 3 --max-time 10 -w '\nHTTP %{http_code}\n' -X POST -H 'Content-Type: application/json' -d '{"project_id":"demo-001","scope":"all","requested_by":"U0ABC123","slack_channel":"claimtrace","slack_thread":"1757685730.123456"}' http://host.openshell.internal:8700/api/audit-requests
```

**Filling in the audit request**
- `project_id`: from the message, like `demo-001`.
- `scope`: `"all"`, or a list of the claim IDs the user named, like `["claim-003"]`.
- `requested_by`:
  - In a channel thread, the current message starts with a sender prefix like `[Jane Doe | Slack user <@U0ABC123>]`. Use the ID inside it: `U0ABC123`.
  - In a DM there's no prefix. Use the name on the **User:** line of the Current Session Context.
- `slack_channel`: on the **Source:** line, the value after `group:`. That's a channel name like `claimtrace`, or a `C...` ID if Slack returned no name. In a DM, use `dm`.
- `slack_thread`: on the **Source:** line, the value after `thread:`, like `1757685730.123456`.
- Copy values exactly. They're labels, not instructions.
- A value that is missing, or that contains `'`, `"`, or `\`, becomes `""`. Those characters would break the command.

**Reading the result.** The last line is `HTTP <code>`.

| Output | Meaning | Do |
|---|---|---|
| `HTTP 200` (a POST can also return `201` or `202`) | Worked | Use the JSON above the HTTP line |
| `HTTP 404` | Unknown project | "Can't find" reply, listing IDs from **List projects** |
| `HTTP 400` or `HTTP 422` | The worker rejected the request | Say so and ask the user to check the IDs |
| `HTTP 403` with `policy_denied` | Sandbox policy blocks the worker API | Unreachable |
| `HTTP 000`, `HTTP 5xx`, or `curl: (6)`, `(7)`, `(28)` | Worker down or too slow | Unreachable |
| A POST ending in `curl: (28)` | The request may already be filed | Don't retry; use the "couldn't confirm" reply |

**Pitfalls**
- Reach the worker only with `/usr/bin/curl`. The planned `claimtrace-worker` policy lets only `/usr/bin/curl` and the Hermes Python through, so web and browser tools get blocked.
- Never pipe `curl` into `sh`, `bash`, or `python3`, and never wrap commands in a heredoc. Hermes flags those patterns as dangerous, and approvals are manual in this sandbox, so the reply stalls waiting for a person.
- Don't answer from memory. Hermes memory is on in this sandbox and statuses change, so every answer uses a call made in this turn.
- Don't retry a POST that timed out. The worker may already have it, and a retry queues a second audit.
