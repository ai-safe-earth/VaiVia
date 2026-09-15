---
name: handoff
description: Update the root handoff.md live-state block and append the session's decisions to docs/pm-log.jsonl. Use only when the owner runs /handoff or explicitly asks for a handoff — never on your own initiative.
---

# Handoff

Three files, one job each. Respect the split or the context cost comes back.

| File | Role | Claude |
|---|---|---|
| `handoff.md` | live state, read at session start | read once, rewrite the block |
| `product-status.md` | non-code progress (branding, strategy, artwork) | append a log row |
| `docs/pm-log.jsonl` | append-only history, one JSON object per line | **append only, never read** |

## When to run

Only when the owner types `/handoff` or asks for it. Do not offer it every turn, do
not run it "because the session feels done" — you cannot detect that. If the owner
says they are about to `/clear` and the handoff is stale, say so in one line.

## Writing the ledger

Every decision, session and settled narrative is one line appended to
`docs/pm-log.jsonl`. Never read the file back, never rewrite it, never sort it.

```bash
printf '%s\n' '{"t":"decision","date":"2026-08-26","phase":"Phase 6 - Beta hardening","plan":"redesign","text":"..."}' >> docs/pm-log.jsonl
printf '%s\n' '{"t":"session","date":"2026-08-26","model":"opus-5","person":"oscar","credits":null,"hours":null}' >> docs/pm-log.jsonl
```

Row types: `decision`, `session`, `note` (narrative worth keeping but not current
state), `idea` (a real next step that is not next). One `session` row per invocation.
`credits` and `hours` stay `null` unless the owner gives numbers — never estimate them.

## Writing handoff.md

Human part first: **state only, 40 lines maximum, no narrative and no history.**

This is the rule that was previously written as "however you like" and the file reached
1,877 lines of dated session diary. A section titled with a date is a `note` row, not a
handoff section. If you are about to write "Session 2026-08-26: what shipped", stop —
that is the ledger's job, and git log already has the detail.

Then replace the block below it, in full.

<!-- pmctl:handoff v1 -->
```json
{
  "project": "VaiVia",
  "org": "ai safe earth",
  "status": "amber",
  "updated": "2026-08-26",
  "deadline": null,
  "people": ["oscar"],
  "plans": [{ "name": "redesign", "path": "docs/", "status": "active" }],
  "phases": [
    { "name": "Phase 6 - Beta hardening", "status": "active", "start": "2026-08-17", "end": null, "plan": "redesign" }
  ],
  "blockers": [{ "text": "...", "severity": "high", "owner": "oscar", "since": "2026-08-01" }],
  "nextSteps": [{ "title": "...", "est": 3, "owner": "oscar", "phase": "Phase 6 - Beta hardening", "plan": "redesign" }],
  "sessions": [{ "date": "2026-08-26", "model": "opus-5", "person": "oscar", "credits": null, "hours": null }]
}
```

**Hard caps — the whole point of the split.**

- No `decisions` key. Decisions go to the ledger, never here.
- `phases`: active and planned only. A phase reaching `done` is deleted from here.
- `nextSteps`: 8 maximum, genuinely next. Anything else is an `idea` row.
- `sessions`: the last 3.
- `blockers`: open only. Resolving one means appending a `decision` row and deleting it here.
- Whole file under 250 lines. If it isn't, you kept history.

Conventions: ISO dates, `null` when unknown. `status` green|amber|red. phase `status`
done|active|planned. `severity` critical|high|medium|low. `est` in working days. No emoji.

## What does not belong in handoff.md

- Run commands, ports, local setup → `CLAUDE.md` or `README.md`.
- Architecture and design doctrine → `docs/architecture.md`.
- Blockers and order of work → the JSON block's `blockers` and `nextSteps`, not prose.

## product-status.md

Non-code progress only. Append a row to its Log table, then refresh its own block:

<!-- pmctl:status v1 -->
```json
{ "project": "VaiVia", "updated": "2026-08-26", "openQuestions": 0, "lastTrack": "product" }
```

## Stop and ask

Personal data, retention, consent, payments or legal text: record it as a blocker with
the decision needed, and stop. Do not decide it.