"""The publication gate: judge every catalogued route against the product's
own limits, so publication is automatic and reversible.

Rules live here, in Python, and not in a table: they are thresholds WITH
reasons, they need a unit test each, and a table of rules needs an
interpreter to read it. Per-route branching that must be tested is Python's
half of the division of labour (CLAUDE.md), and CI has no PostGIS — so
`judge` is pure and the database only stores what it decided.

Three verdicts, and 'held' is the load-bearing one. A route whose facts were
never measured is HELD, never passed: absent is not zero here either, and a
NULL urban_share must not publish a town walk by omission. Held is also
where every route starts, which is what makes the first calibration round
possible — nothing is served until a ruleset has actually spoken.

Re-judging is the point, not an afterthought. The thresholds below are
PROVISIONAL (version 'draft-0'): they are placed at boundaries the corpus
already measured, not at ratified product limits, and the first review round
exists to move them. Bump GATE_VERSION when they change and re-run; a route
that used to pass and now fails is demoted, and the next emit takes its
document out of review/routes/ and out of Neo4j.

    uv run python -m curate.gate --dry-run   # judge, write nothing
    uv run python -m curate.gate             # commit the verdicts
"""

from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Mapping

from core import connect

#: Bump on ANY change to the thresholds or the rule set. Stored per route, so
#: a row always says which ruleset judged it — and a corpus judged by two
#: different versions is visible instead of silently mixed.
GATE_VERSION = "draft-0"

#: Provisional, pending the first review round.
#: - urban: the measured distribution (0008, 228 routes) is median 0.29,
#:   p80 0.55, p90 0.62. The factory's own limit is 0.8 — the tail that is
#:   ENTIRELY a town walk. The catalogue asks for more than that, so this
#:   sits at p80 until the review round says otherwise.
#: - paved: the owner's stated example, "asphalt not more than 40%".
#:
#: An off-road rule (highway type, qa's `offroad_class` boundary of 30%) was
#: written and DROPPED: measured over the 627 routes it caught 3 that the
#: paved rule did not, and 266 that it already had. Surface and highway type
#: say the same thing about this corpus, and two thresholds that move
#: together are one threshold with a second place to go wrong. It is a
#: function and a tuple entry if the review round disagrees.
MAX_URBAN_SHARE = 0.55
MAX_PAVED_SHARE = 0.40

#: What a walker feels underfoot as road, whatever OSM called it.
PAVED_SURFACES = ("asphalt", "concrete", "paved", "paving_stones", "sett")

#: Facts no rule can do without. NULL is "not measured", so the route is
#: held rather than judged on a fact nobody has.
REQUIRED = ("urban_share", "surface")


def paved_share(surface: Mapping[str, float]) -> float:
    """Length-weighted share of the walk on a made surface. 'unknown' counts
    as neither — it is untagged ground, not a road."""
    return sum(surface.get(name, 0.0) for name in PAVED_SURFACES)


def _urban(route: Mapping) -> str | None:
    share = route["urban_share"]
    if share > MAX_URBAN_SHARE:
        return f"urban share {share:.0%} over {MAX_URBAN_SHARE:.0%}"
    return None


def _paved(route: Mapping) -> str | None:
    share = paved_share(route["surface"])
    if share > MAX_PAVED_SHARE:
        return f"paved share {share:.0%} over {MAX_PAVED_SHARE:.0%}"
    return None


RULES = (_urban, _paved)


def judge(route: Mapping) -> tuple[str, list[str]]:
    """A route's facts in, its verdict and every reason out.

    Every rule runs — the reasons are the calibration surface, and stopping
    at the first failure would hide which threshold is actually doing the
    work when they are being tuned.
    """
    missing = [f"{fact} not measured" for fact in REQUIRED if route.get(fact) is None]
    if missing:
        return "held", missing
    reasons = [reason for rule in RULES if (reason := rule(route)) is not None]
    return ("fail" if reasons else "pass"), reasons


ROUTES = """
SELECT route_id, urban_share, surface, gate_verdict
FROM catalogue.route
ORDER BY route_id
"""

WRITE = """
UPDATE catalogue.route
SET gate_verdict = %(verdict)s,
    gate_reasons = %(reasons)s,
    gate_version = %(version)s
WHERE route_id = %(route_id)s
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with connect() as conn:
        rows = conn.execute(ROUTES).fetchall()
        if not rows:
            raise SystemExit("no routes in catalogue.route — nothing to judge")

        verdicts: dict[str, int] = {}
        moved: dict[str, int] = {}
        why: dict[str, int] = {}
        updates = []
        for route_id, urban_share, surface, was in rows:
            verdict, reasons = judge(
                {
                    "urban_share": urban_share,
                    "surface": surface,
                }
            )
            verdicts[verdict] = verdicts.get(verdict, 0) + 1
            if verdict != was:
                moved[f"{was} -> {verdict}"] = moved.get(f"{was} -> {verdict}", 0) + 1
            for reason in reasons:
                # Group on the rule, not the measured value: "urban share 62%
                # over 55%" and "... 71% over 55%" are one threshold talking.
                head = reason.split(" over ")[0].split(" under ")[0]
                rule = head.rsplit(" ", 1)[0] if head != reason else reason
                why[rule] = why.get(rule, 0) + 1
            updates.append(
                {
                    "route_id": route_id,
                    "verdict": verdict,
                    "reasons": json.dumps(reasons) if reasons else None,
                    "version": GATE_VERSION,
                }
            )

        print(f"gate {GATE_VERSION} over {len(rows):,} routes")
        for verdict in ("pass", "held", "fail"):
            print(f"  {verdicts.get(verdict, 0):,} {verdict}")
        for reason, n in sorted(why.items(), key=lambda kv: -kv[1]):
            print(f"    {n:,} {reason}")
        # A demotion is the expensive half of this and must never be quiet.
        for move, n in sorted(moved.items()):
            print(f"  {n:,} {move}")

        if args.dry_run:
            print("--dry-run: nothing written")
            return

        run_id = f"curate-gate-{uuid.uuid4().hex[:8]}"
        with conn.transaction():
            conn.cursor().executemany(WRITE, updates)
            conn.execute(
                "INSERT INTO provenance.build_run"
                " (run_id, stage, parameters, counts, finished_at)"
                " VALUES (%s, 'curate', %s, %s, now())",
                (
                    run_id,
                    json.dumps(
                        {
                            "builder": "curate.gate",
                            "gate_version": GATE_VERSION,
                            "max_urban_share": MAX_URBAN_SHARE,
                            "max_paved_share": MAX_PAVED_SHARE,
                        }
                    ),
                    json.dumps({"routes": len(rows), **verdicts, "moved": moved}),
                ),
            )
        print(f"run {run_id}: verdicts written")
        print("next: uv run python -m draw.emit && uv run python -m export.neo4j_load")


if __name__ == "__main__":
    main()
