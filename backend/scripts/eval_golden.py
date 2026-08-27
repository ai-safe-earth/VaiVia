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

Every run appends one JSON line (date, commit, scores) to eval_runs.jsonl in
backend/, so scores stay comparable across prompt changes.

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
        elif key.startswith(("search.", "loop.")):
            prefix, field = key.split(".", 1)
            holder = plan.search if prefix == "search" else plan.loop
            got = getattr(holder, field, None) if holder else None
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
    # The prompt asks for the best one or two loops by name, not all of them
    # (relaxed 2026-08-27: demanding every name made the model choose between
    # coverage and brevity). Case-insensitive: a destination route is named
    # "To Monte X" and the prompt itself says to write "out and back to
    # Monte X". Whether a mentioned name is EXACTLY as given is judge
    # territory; this checks that loops were presented by name at all.
    lowered = answer.lower()
    names = {loop["name"] for loop in view.get("loops") or [] if loop.get("name")}
    if names and not any(name.lower() in lowered for name in names):
        problems.append(f"no loop named; the view offered {sorted(names)}")
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
    args = parser.parse_args()
    if args.answers and not args.graph:
        parser.error("--answers needs --graph: answers are grounded in results")

    entries = json.loads(DATASET.read_text(encoding="utf-8"))
    client = OpenAIClient()

    orchestrator = None
    db = None
    if args.graph:
        from core.embeddings import OpenAIEmbedder
        from graph.neo4j_client import Neo4jClient

        db = Neo4jClient()
        await db.connect()
        # llm/store stay None: _execute never touches them, and going through
        # run() would drag in the store and its quota pre-check.
        orchestrator = ChatOrchestrator(
            db=db, llm=None, store=None, embedder=OpenAIEmbedder()
        )

    plan_pass = plan_total = 0
    hit_first = hit_any = retrieval_total = 0
    answer_pass = answer_total = 0
    try:
        for entry in entries:
            turns = entry.get("turns") or [entry["question"]]
            plan, kinds = await run_turns(client, turns)
            problems = check_plan(entry.get("expect", {}), plan)
            plan_total += 1
            plan_pass += not problems
            label = " | ".join(turns)
            print(
                f"[{'PASS' if not problems else 'FAIL'}] {entry['id']} "
                f"{label!r} -> {kinds}"
            )
            for problem in problems:
                print(f"         {problem}")

            expected_trails = entry.get("expect_trails") or []
            expected_loops = entry.get("expect_loops") or []
            wants_execution = expected_trails or expected_loops or args.answers
            if not (args.graph and wants_execution and not plan.is_clarify):
                continue

            results, _ = await orchestrator._execute(
                plan
            )  # noqa: SLF001 — eval reuses the real path
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
    if args.answers:
        print(f"answers:       {answer_pass}/{answer_total} passed")
        summary["answers"] = [answer_pass, answer_total]
    log_run(summary)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
