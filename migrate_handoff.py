"""Split handoff.md into live state + an append-only ledger.

Run once from the repo root:  python migrate_handoff.py
  - dated diary sections  ->  docs/pm-log.jsonl  as {"t":"note"}
  - phase decisions       ->  docs/pm-log.jsonl  as {"t":"decision"}
  - sessions              ->  docs/pm-log.jsonl  as {"t":"session"}
  - done phases dropped from handoff.md (their decisions are in the ledger)
Nothing is deleted: every removed byte lands in the ledger. Commit before running.
"""

import json
import re
from pathlib import Path

MARK = "<!-- pmctl:handoff v1 -->"
DATED = re.compile(r"^(Session\s+)?\d{4}-\d{2}-\d{2}|^Session\s", re.I)
KEEP_STEPS, KEEP_SESSIONS = 8, 3

src = Path("handoff.md")
raw = src.read_text(encoding="utf8")
head, block = raw.split(MARK, 1)
data = json.loads(re.search(r"```json\n(.*)\n```", block, re.S).group(1))
ledger = []

# --- prose: dated sections are history, everything else stays ---
parts = re.split(r"^## ", head, flags=re.M)
kept = [parts[0]]
for sec in parts[1:]:
    title = sec.split("\n", 1)[0].strip()
    if DATED.match(title):
        found = re.search(r"\d{4}-\d{2}-\d{2}", title)
        ledger.append({"t": "note", "date": found.group(0) if found else None,
                       "title": title, "text": sec.split("\n", 1)[-1].strip()})
    else:
        kept.append("## " + sec)

# --- json: decisions and sessions are history ---
for phase in data.get("phases", []):
    for d in phase.pop("decisions", []):
        ledger.append({"t": "decision", "date": d["date"], "phase": phase["name"],
                       "plan": phase.get("plan"), "text": d["text"]})
for s in data.pop("sessions", []):
    ledger.append({"t": "session", **s})

# --- nextSteps beyond the cap are ideas, not next steps ---
steps = data.get("nextSteps", [])
for s in steps[KEEP_STEPS:]:
    ledger.append({"t": "idea", "date": data.get("updated"), **s})
data["nextSteps"] = steps[:KEEP_STEPS]

ledger.sort(key=lambda r: r.get("date") or "")
out = Path("docs/pm-log.jsonl")
out.parent.mkdir(exist_ok=True)
with out.open("a", encoding="utf8") as f:      # append: never clobber an existing ledger
    for r in ledger:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

data["phases"] = [p for p in data.get("phases", []) if p.get("status") != "done"]
data["sessions"] = [r for r in ledger if r["t"] == "session"][-KEEP_SESSIONS:]
for s in data["sessions"]:
    s.pop("t", None)

src.write_text("".join(kept).rstrip() + "\n\n" + MARK + "\n```json\n"
               + json.dumps(data, indent=2, ensure_ascii=False) + "\n```\n", encoding="utf8")

prose = len("".join(kept))
print(f"ledger rows: {len(ledger)}  ({out.stat().st_size} B)")
print(f"handoff.md:  {len(raw)} B -> {src.stat().st_size} B")
print(f"prose left:  {prose} B in {len(kept) - 1} sections — target is ~2000 B / 40 lines")
if prose > 4000:
    print("  ^ still over. Rewrite by hand: the mechanical pass cannot tell")
    print("    current state from settled narrative.")