---
name: meeting-prep
description: Prepare a briefing for an upcoming meeting from the project's own files — what changed since last time, what the attendees will ask about, and what is still unresolved. Use when asked to prep for a meeting, draft an agenda, or catch up before a sync, and when a scheduled meeting is approaching.
---

# Meeting prep

You turn a project's local files into a briefing someone can read in two minutes
before walking into a meeting.

## Absolute rules

1. **Everything comes from local files.** No general knowledge, no invented
   context about the attendees, no guesses about what they want.
2. **Cite sources.** Every factual line carries `` `path` (page N) ``.
3. **Never compute a number.** Quantitative points come from recorded
   verifications; use the `claimtrace` skill if something needs checking first.
4. **Short.** A briefing longer than a page will not be read. Cut background.

## Tools

| Command | Use |
|---|---|
| `labmate --project P meeting-list [--within-days 7]` | What is scheduled. |
| `labmate --project P meeting-set --title T --when W --topics ...` | Add or edit a meeting. |
| `labmate --project P meeting-brief --id M` | Assemble context: passages per topic, recent changes, open items. |
| `labmate --project P meeting-brief --id M --summary "<markdown>"` | Save the briefing you wrote. |
| `labmate --project P ask --query Q` | Fill a gap the context missed. |
| `labmate --project P digest` | Broader picture if the meeting has no topics. |

## Workflow

1. `meeting-list` if you were not given a meeting id. If more than one is close,
   take the soonest and say which you picked.
2. `meeting-brief --id <id>` with no `--summary`. This returns the context
   bundle: retrieved passages per topic, files changed in the window, and open
   items from earlier analysis. Do not retrieve separately first; this does it.
3. If a topic returned no passages, `ask` once with different phrasing. If still
   nothing, that absence goes in the briefing — "no local source covers X" is
   one of the most useful lines you can write.
4. Write the briefing in this shape:

   ```markdown
   ## Where things stand
   Two or three sentences. The single most important fact first.

   ## Since we last met
   - What changed, with citations. Skip if nothing changed.

   ## Needs a decision
   - The open question, the options, and what you would do.

   ## Likely questions
   - What an attendee will ask, and the cited answer.
   ```

5. Save it: `meeting-brief --id <id> --summary "<the markdown>"`. The tool
   appends the sources, the change list, and the open items automatically — do
   not repeat them in your summary.
6. `notify --reason "brief ready for <meeting id>"`.

## What makes a briefing good

The "Needs a decision" section is the whole point. Anyone can list what
happened. State the thing that is unresolved, what the options are, and lean
toward one. If earlier analysis found a conflict — a reported number that no
longer matches the data — that is the headline, not a footnote.
