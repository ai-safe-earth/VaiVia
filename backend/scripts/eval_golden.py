"""Golden-dataset evaluation: question -> plan -> composed search -> retrieval.

Measures the stages of the chat pipeline separately, so a failure points at
the layer that caused it:

  DECOMPOSITION  Does the live model break the question into the right atomic
                 subqueries, and does the composer merge them as expected?
                 (needs OPENAI_API_KEY; costs a few cents)
  RETRIEVAL      With --graph, execute each composed plan against the live
                 graph exactly as the orchestrator does and check the expected
                 trail / catalogue loop is ranked first / retrieved. Needs
                 Neo4j up, ingestion done, and the embedding job run.
  ANSWER         With --answers (requires --graph), stream the answer for the
                 executed results and run the code-checkable prompt rules over
                 the RAW model text — before strip_links_stream, because
                 production repairs what this measures: whether the model
                 obeys. Judge-territory rules (no invented facts, empty-result
                 phrasing, semantic_unavailable wording) are deliberately not
                 checked here; they need error analysis first.

Run from backend/:
    uv run python -m scripts.eval_golden                     # decomposition only
    uv run python -m scripts.eval_golden --graph             # + live retrieval
    uv run python -m scripts.eval_golden --graph --answers   # + answer checks
    uv run python -m scripts.eval_golden --only g27,g49      # rerun two entries

Every FULL run appends one JSON line to eval_runs.jsonl in backend/: date,
commit, the scores, and — since 2026-08-29 — which entries failed and how each
retrieval expectation landed. The aggregates say a score moved; the per-entry
maps say WHICH question moved, which is the difference between a trend and a
lead. Both live on the same line, so `tail -2` is the whole diff tooling.

--only runs a subset and deliberately does NOT log: a partial total in the
trend is worse than a gap in it. Use it to read one failure again without
paying for the other forty-nine.

Dataset: fixtures/golden_questions.json. Expectations address the COMPOSED
plan ("search.<field>", "loop.<field>", "theme", "routes", "clarify");
"expect_trails" names the trail ids retrieval must surface, "expect_loops"
the catalogue route ids (geometry-stable vv2-…, never names — names are
rewritten every name_routes run); first id = must rank first. An entry with
"turns" instead of "question" is a CONVERSATION: each turn runs through the
same extract -> compose -> apply_delta loop the orchestrator uses, the
standing plan threading between turns, and the expectation addresses the
final turn's plan. A numeric expectation may be {"lt": x} / {"lte": x} /
{"gt": x} where a refinement's exact number is the model's to choose
("shorter") but its direction is not.
"""

import argparse
import asyncio
import datetime
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from chat.composer import (
    ComposedPlan,
    apply_delta,
    compose,
    standing_dump,
    standing_load,
)
from chat.llm import OpenAIClient, results_to_json
from chat.orchestrator import ChatOrchestrator, _answer_view  # noqa: SLF001
from chat.sanitize import find_link

DATASET = Path(__file__).resolve().parent.parent / "fixtures" / "golden_questions.json"
RUN_LOG = Path(__file__).resolve().parent.parent / "eval_runs.jsonl"

#: Where the full pack for expect_facts entries lives (the parity export);
#: override with VAIVIA_PACK_DIR. Facts entries are PINNED to a pack run_id —
#: bands measured on one network are meaningless on another — and are
#: reported as stale, not failed, when the loaded pack is a different build.
import os  # noqa: E402

PACK_DIR = Path(
    os.environ.get(
        "VAIVIA_PACK_DIR",
        Path(__file__).resolve().parents[2] / "pipeline" / "packs" / "pack-parity",
    )
)


def check_facts(expected: dict[str, Any], results: dict[str, Any]) -> list[str]:
    """Band checks over the FIRST drawn card — distance, ascent, surface
    shares, ends-at kind, car-free — the planner's honesty, measured."""
    cards = results.get("loops") or []
    if not cards:
        return [f"no routes drawn (counts: {results.get('counts')})"]
    card = cards[0]
    problems: list[str] = []
    for key, want in expected.items():
        got: Any = card
        for part in key.split("."):
            got = got.get(part) if isinstance(got, dict) else None
        if isinstance(want, dict) and want.keys() & {"lt", "lte", "gt", "gte"}:
            ok = (
                got is not None
                and ("lt" not in want or got < want["lt"])
                and ("lte" not in want or got <= want["lte"])
                and ("gt" not in want or got > want["gt"])
                and ("gte" not in want or got >= want["gte"])
            )
            if not ok:
                problems.append(f"facts {key}: want {want}, got {got}")
        elif isinstance(want, list):
            if got not in want:
                problems.append(f"facts {key}: want one of {want}, got {got}")
        elif got != want:
            problems.append(f"facts {key}: want {want}, got {got}")
    return problems


def select(entries: list[dict[str, Any]], only: str | None) -> list[dict[str, Any]]:
    """The entries this run covers.

    An id that is not in the dataset is a typo, and a typo that silently ran
    zero entries would print a clean 0/0 and read as a pass.
    """
    if not only:
        return entries
    wanted = [part.strip() for part in only.split(",") if part.strip()]
    known = {entry["id"] for entry in entries}
    unknown = [name for name in wanted if name not in known]
    if unknown:
        raise SystemExit(f"--only: no such entry id: {', '.join(unknown)}")
    return [entry for entry in entries if entry["id"] in set(wanted)]


def check_plan(expected: dict[str, Any], plan: ComposedPlan) -> list[str]:
    problems: list[str] = []
    if expected.get("clarify"):
        if not plan.is_clarify:
            problems.append("expected clarify, plan is executable")
        return problems
    if plan.is_clarify:
        assert plan.clarify is not None
        return [f"composed to clarify: {plan.clarify.question!r}"]
    for key, want in expected.items():
        if key == "theme":
            if bool(plan.theme) != want:
                problems.append(f"theme: want present={want}, got {plan.theme!r}")
        elif key == "routes":
            if len(plan.routes) != want:
                problems.append(f"routes: want {want}, got {len(plan.routes)}")
        elif key.startswith(("search.", "loop.", "outing.")):
            prefix, field = key.split(".", 1)
            holder = {
                "search": plan.search,
                "loop": plan.loop,
                "outing": plan.outing,
            }[prefix]
            # Dotted tails walk nested models: "outing.start.mode".
            got = holder
            for part in field.split("."):
                got = getattr(got, part, None) if got is not None else None
            if isinstance(want, list):
                if not set(want) <= set(got or []):
                    problems.append(f"{key}: want superset of {want}, got {got}")
            elif isinstance(want, dict) and want.keys() & {"lt", "lte", "gt"}:
                ok = (
                    got is not None
                    and ("lt" not in want or got < want["lt"])
                    and ("lte" not in want or got <= want["lte"])
                    and ("gt" not in want or got > want["gt"])
                )
                if not ok:
                    problems.append(f"{key}: want {want}, got {got}")
            elif got != want:
                problems.append(f"{key}: want {want}, got {got}")
    return problems


def check_answer(answer: str, view: dict[str, Any]) -> list[str]:
    """The code-checkable rules of ANSWER_SYSTEM_PROMPT, over the raw answer.

    ``view`` is what the model was shown — _answer_view(results), the top-5
    prefix — so what the view holds is what the answer had to work from.
    """
    problems: list[str] = []
    link = find_link(answer)
    if link is not None:
        problems.append(f"link in answer: {link!r}")
    if "trailforks" in answer.lower():
        problems.append("names trailforks")
    if any(line.lstrip().startswith("#") for line in answer.splitlines()):
        problems.append("markdown header in answer")
    # The count rule (owner decision 2026-08-27): the reply is a count-first
    # sentence, so when the view carries a total the answer must state it as
    # digits — a count the cards contradict is worse than none. The prompt
    # demands BOTH totals when both are present, so both are checked.
    # Digit-bounded, not a substring: total 5 must not pass on "15 routes" or
    # "12.5 km"; commas are stripped first so "1,035" still states 1035.
    # Zero is exempt: the empty-block rule asks for "nothing matched" prose,
    # and demanding the digit 0 would punish the answer the prompt requires.
    # (Residual leniency: when both totals are the same number, one mention
    # satisfies both — indistinguishable without parsing the sentence.)
    plain = answer.replace(",", "")
    for field in ("total_loops", "total_trails"):
        total = view.get(field)
        if total and not re.search(rf"(?<!\d)(?<!\d\.){total}(?!\.?\d)", plain):
            problems.append(f"count missing: answer must state {total} ({field})")
    return problems


async def run_turns(
    client: OpenAIClient, turns: list[str]
) -> tuple[ComposedPlan, list[str]]:
    """The orchestrator's multiturn loop, minus the store: the standing plan
    threads between turns exactly as chat/orchestrator.py threads it."""
    standing_raw = None
    plan = ComposedPlan()
    kinds: list[str] = []
    for turn in turns:
        result = await client.extract_plan(turn, [], standing=standing_raw)
        subqueries = result.envelope.subqueries
        plan = compose(subqueries)
        kinds = [s.kind for s in subqueries]
        if result.envelope.refine and not plan.is_clarify:
            base = standing_load(standing_raw)
            if base is not None:
                plan = apply_delta(base, plan)
        if not plan.is_clarify:
            standing_raw = standing_dump(plan)
    return plan, kinds


def log_run(summary: dict[str, Any]) -> None:
    """One JSON line per run, appended: the trend the handoff prose cannot hold."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    line = {"date": datetime.date.today().isoformat(), "commit": commit, **summary}
    with RUN_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--graph",
        action="store_true",
        help="also execute composed plans against the live graph",
    )
    parser.add_argument(
        "--answers",
        action="store_true",
        help="also stream answers and run the code-checkable prompt rules",
    )
    parser.add_argument(
        "--only",
        metavar="IDS",
        help="run only these dataset ids (comma-separated); not logged",
    )
    args = parser.parse_args()
    if args.answers and not args.graph:
        parser.error("--answers needs --graph: answers are grounded in results")

    entries = select(json.loads(DATASET.read_text(encoding="utf-8")), args.only)
    client = OpenAIClient()

    orchestrator = None
    db = None
    planner = None
    if args.graph:
        from core.embeddings import OpenAIEmbedder
        from graph.neo4j_client import Neo4jClient

        if PACK_DIR.is_dir():
            from chat.pack_state import load_planner

            planner = load_planner(str(PACK_DIR))
        db = Neo4jClient()
        await db.connect()
        # llm/store stay None: _execute never touches them, and going through
        # run() would drag in the store and its quota pre-check.
        orchestrator = ChatOrchestrator(
            db=db, llm=None, store=None, embedder=OpenAIEmbedder(), planner=planner
        )

    plan_pass = plan_total = 0
    hit_first = hit_any = retrieval_total = 0
    facts_pass = facts_total = facts_stale = 0
    answer_pass = answer_total = 0
    # What the aggregates cannot say: which entry. Stage-prefixed so a line
    # tells decomposition drift from answer drift without rerunning anything.
    failed: dict[str, list[str]] = {}
    retrieval_marks: dict[str, str] = {}
    try:
        for entry in entries:
            turns = entry.get("turns") or [entry["question"]]
            plan, kinds = await run_turns(client, turns)
            problems = check_plan(entry.get("expect", {}), plan)
            plan_total += 1
            plan_pass += not problems
            if problems:
                failed[entry["id"]] = [f"plan: {problem}" for problem in problems]
            label = " | ".join(turns)
            print(
                f"[{'PASS' if not problems else 'FAIL'}] {entry['id']} "
                f"{label!r} -> {kinds}"
            )
            for problem in problems:
                print(f"         {problem}")

            expected_trails = entry.get("expect_trails") or []
            expected_loops = entry.get("expect_loops") or []
            expected_facts = entry.get("expect_facts") or {}
            if expected_facts and args.graph:
                if plan.outing is None:
                    # facts measure the PLANNER; a turn that decomposed away
                    # from outing already failed at the decomposition stage.
                    facts_total += 1
                    failed.setdefault(entry["id"], []).append(
                        "facts: no outing decomposed"
                    )
                    print("         facts [FAIL]: no outing decomposed")
                    expected_facts = {}
                elif planner is None:
                    print("         facts [SKIP]: no pack loaded")
                    expected_facts = {}
                elif entry.get("pack_run_id") not in (None, planner.run_id):
                    # A band measured on another network is not a failure of
                    # this one; it is a fixture waiting to be re-pinned.
                    facts_stale += 1
                    print(
                        f"         facts [STALE]: pinned to "
                        f"{entry.get('pack_run_id')!r}, loaded {planner.run_id!r}"
                    )
                    expected_facts = {}
            wants_execution = (
                expected_trails or expected_loops or expected_facts or args.answers
            )
            if not (args.graph and wants_execution and not plan.is_clarify):
                continue

            results, _ = await orchestrator._execute(
                plan
            )  # noqa: SLF001 — eval reuses the real path
            if expected_facts:
                facts_problems = check_facts(expected_facts, results)
                facts_total += 1
                facts_pass += not facts_problems
                print(f"         facts [{'PASS' if not facts_problems else 'FAIL'}]")
                for problem in facts_problems:
                    print(f"         {problem}")
                if facts_problems:
                    failed.setdefault(entry["id"], []).extend(facts_problems)
            for expected, key in (
                (expected_trails, "trails"),
                (expected_loops, "loops"),
            ):
                if not expected:
                    continue
                retrieved = [r["id"] for r in results.get(key) or []]
                retrieval_total += 1
                first_ok = bool(retrieved) and retrieved[0] == expected[0]
                any_ok = set(expected) <= set(retrieved)
                hit_first += first_ok
                hit_any += any_ok
                marker = "first" if first_ok else ("hit" if any_ok else "MISS")
                # Keyed by entry AND block: one entry may pin both, and "g30
                # missed" would not say which half.
                retrieval_marks[f"{entry['id']}.{key}"] = marker.lower()
                print(f"         {key} [{marker}]: {retrieved}")

            if args.answers:
                view = _answer_view(results)
                answer = "".join(
                    [
                        chunk
                        async for chunk in client.stream_answer(
                            turns[-1], results_to_json(view), []
                        )
                    ]
                )
                answer_problems = check_answer(answer, view)
                answer_total += 1
                answer_pass += not answer_problems
                if answer_problems:
                    failed.setdefault(entry["id"], []).extend(
                        f"answer: {problem}" for problem in answer_problems
                    )
                print(f"         answer [{'PASS' if not answer_problems else 'FAIL'}]")
                for problem in answer_problems:
                    print(f"         {problem}")
    finally:
        if db is not None:
            await db.close()

    print(f"\ndecomposition: {plan_pass}/{plan_total} passed")
    summary: dict[str, Any] = {
        "entries": plan_total,
        "decomposition": [plan_pass, plan_total],
    }
    if args.graph:
        print(
            f"retrieval:     {hit_any}/{retrieval_total} retrieved, "
            f"{hit_first}/{retrieval_total} ranked first"
        )
        summary["retrieval_hit"] = [hit_any, retrieval_total]
        summary["retrieval_first"] = [hit_first, retrieval_total]
        if facts_total or facts_stale:
            print(
                f"facts:         {facts_pass}/{facts_total} passed"
                + (f", {facts_stale} stale" if facts_stale else "")
            )
            summary["facts"] = [facts_pass, facts_total]
            if facts_stale:
                summary["facts_stale"] = facts_stale
    if args.answers:
        print(f"answers:       {answer_pass}/{answer_total} passed")
        summary["answers"] = [answer_pass, answer_total]
    # Last, so the aggregates stay at the head of the line where a human reads
    # them; the maps are what you grep once a number has moved.
    summary["failed"] = failed
    if args.graph:
        summary["retrieval"] = retrieval_marks
    if args.only:
        print("\n--only: partial run, not appended to eval_runs.jsonl")
        return
    log_run(summary)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
