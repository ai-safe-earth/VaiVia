"""Golden-dataset evaluation: question -> plan -> composed search -> retrieval.

Measures the two halves of the chat pipeline separately, so a failure points at
the layer that caused it:

  DECOMPOSITION  Does the live model break the question into the right atomic
                 subqueries, and does the composer merge them as expected?
                 (needs OPENAI_API_KEY; costs a few cents)
  RETRIEVAL      With --graph, run each composed search against the live graph
                 (structured, or vector when a theme is present) and check the
                 expected trail is ranked first / retrieved. Needs Neo4j up,
                 ingestion done, and the embedding job run.

Run from backend/:
    uv run python -m scripts.eval_golden            # decomposition only
    uv run python -m scripts.eval_golden --graph    # + live retrieval

Dataset: fixtures/golden_questions.json. Expectations address the COMPOSED
plan ("search.<field>", "theme", "routes", "clarify"); "expect_trails" names
the trail ids retrieval must surface, first id = must rank first.
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from chat.composer import ComposedPlan, compose
from chat.intents import TrailSearchIntent
from chat.llm import OpenAIClient

DATASET = Path(__file__).resolve().parent.parent / "fixtures" / "golden_questions.json"


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
        elif key.startswith("search."):
            field = key.removeprefix("search.")
            got = getattr(plan.search, field, None) if plan.search else None
            if isinstance(want, list):
                if not set(want) <= set(got or []):
                    problems.append(f"{key}: want superset of {want}, got {got}")
            elif got != want:
                problems.append(f"{key}: want {want}, got {got}")
    return problems


async def run_retrieval(plan: ComposedPlan, db: Any, embedder: Any) -> list[str]:
    """Execute the composed search exactly the way the orchestrator does."""
    from chat.orchestrator import ChatOrchestrator

    orchestrator = ChatOrchestrator(db=db, llm=None, store=None, embedder=embedder)
    rows, _ = await orchestrator._search(  # noqa: SLF001 — eval reuses the real path
        plan.search or TrailSearchIntent(), plan.theme
    )
    return [r["id"] for r in rows]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--graph",
        action="store_true",
        help="also run composed searches against the live graph",
    )
    args = parser.parse_args()

    entries = json.loads(DATASET.read_text(encoding="utf-8"))
    client = OpenAIClient()

    db = embedder = None
    if args.graph:
        from core.embeddings import OpenAIEmbedder
        from graph.neo4j_client import Neo4jClient

        db = Neo4jClient()
        await db.connect()
        embedder = OpenAIEmbedder()

    plan_pass = plan_total = 0
    hit_first = hit_any = retrieval_total = 0
    try:
        for entry in entries:
            result = await client.extract_plan(entry["question"], [])
            subqueries = result.envelope.subqueries
            plan = compose(subqueries)
            problems = check_plan(entry.get("expect", {}), plan)
            plan_total += 1
            plan_pass += not problems
            kinds = [s.kind for s in subqueries]
            print(
                f"[{'PASS' if not problems else 'FAIL'}] {entry['id']} "
                f"{entry['question']!r} -> {kinds}"
            )
            for problem in problems:
                print(f"         {problem}")

            expected_trails = entry.get("expect_trails") or []
            if args.graph and expected_trails and not plan.is_clarify:
                retrieved = await run_retrieval(plan, db, embedder)
                retrieval_total += 1
                first_ok = bool(retrieved) and retrieved[0] == expected_trails[0]
                any_ok = set(expected_trails) <= set(retrieved)
                hit_first += first_ok
                hit_any += any_ok
                marker = "first" if first_ok else ("hit" if any_ok else "MISS")
                print(f"         retrieval [{marker}]: {retrieved}")
    finally:
        if db is not None:
            await db.close()

    print(f"\ndecomposition: {plan_pass}/{plan_total} passed")
    if args.graph:
        print(
            f"retrieval:     {hit_any}/{retrieval_total} retrieved, "
            f"{hit_first}/{retrieval_total} ranked first"
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
