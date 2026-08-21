"""Measure where a theme's matches stop being matches.

``db.index.vector.queryNodes`` has no notion of "nothing here is close": it
returns its nearest neighbours however distant they are. So the theme path
needs a floor, and this measures where it goes rather than guessing -- the same
rule the pipeline follows for every tolerance (a distribution, not a hunch).

The floor is RELATIVE to each query's best match, because a normalized cosine
score has no absolute bright line: the whole corpus sits in a narrow band whose
position moves with the query. What this measures, per theme, is the DROP from
the top score at which the ranking falls off -- the biggest gap between
consecutive scores in the pool ("the knee"). SEMANTIC_SCORE_DROP wants to sit
at or just past the typical knee: far enough to keep the cluster of real
matches, short enough to cut the tail behind it.

Costs a few cents (one embedding per theme). Needs a populated index:

    uv run python -m scripts.embed_trails
    uv run python -m scripts.calibrate_semantic_drop
"""

import asyncio
import statistics

from chat.orchestrator import SEMANTIC_CANDIDATE_POOL, SEMANTIC_SCORE_DROP
from core.embeddings import OpenAIEmbedder
from graph.neo4j_client import Neo4jClient

#: The themes the golden dataset asks, plus two deliberately off-corpus ones.
#: A query with NO good answer is the case the floor exists for, so the
#: measurement has to include some.
THEMES = [
    "exposed alpine ridgeline above the tree line",
    "shaded lakefront promenade with picnic spots",
    "rocky balcony singletrack with open views over Lake Como",
    "chestnut forest and gravel by the water",
    "big alpine day out through larch forest and meadows",
    "a grassy ridgeline with a summit cross above the city walls",
    "gravel between vineyards with views of the Venetian walls",
    "shady forest trails",
    # Off-corpus: nothing in Lecco or Bergamo answers these.
    "a coral reef dive with sea turtles",
    "a desert canyon slot with fixed ropes",
]

#: Candidate cuts to score against the measurement.
CANDIDATES = [0.02, 0.03, 0.05, 0.08, 0.12, None]


async def main() -> None:
    embedder = OpenAIEmbedder()
    knees: list[float] = []

    async with Neo4jClient() as client:
        status = await client.run_read(
            "MATCH (t:Trail) WHERE t.description_embedding IS NOT NULL "
            "RETURN count(t) AS embedded"
        )
        embedded = status[0]["embedded"] if status else 0
        print(f"embedded trails = {embedded}")
        if not embedded:
            print("index is empty - run scripts.embed_trails first")
            return
        print(
            f"pool = {SEMANTIC_CANDIDATE_POOL}, current cut = {SEMANTIC_SCORE_DROP}\n"
        )

        kept: dict[float | None, list[int]] = {c: [] for c in CANDIDATES}
        for theme in THEMES:
            [embedding] = await embedder.embed_texts([theme])
            rows = await client.run_read(
                "CALL db.index.vector.queryNodes("
                "'trail_embeddings', $pool, $embedding) "
                "YIELD node AS t, score "
                "RETURN t.name AS name, score ORDER BY score DESC",
                pool=SEMANTIC_CANDIDATE_POOL,
                embedding=embedding,
            )
            scores = [row["score"] for row in rows]
            if len(scores) < 2:
                print(f"{theme!r}: {len(scores)} row(s), skipped")
                continue

            top = scores[0]
            gaps = [(scores[i] - scores[i + 1], i) for i in range(len(scores) - 1)]
            widest, at = max(gaps)
            knee = top - scores[at]  # how far below the top the knee sits
            knees.append(knee)

            for candidate in CANDIDATES:
                if candidate is None:
                    kept[candidate].append(len(scores))
                else:
                    kept[candidate].append(
                        sum(1 for s in scores if s >= top - candidate)
                    )

            print(f"{theme!r}")
            print(
                f"    top={top:.4f} last={scores[-1]:.4f} "
                f"span={top - scores[-1]:.4f} "
                f"knee after #{at + 1} (gap {widest:.4f}, {knee:.4f} below top)"
            )
            print(
                "    scores: "
                + " ".join(f"{s:.3f}" for s in scores[:12])
                + (" ..." if len(scores) > 12 else "")
            )

    if not knees:
        return
    print(f"\nknee distance below the top score, over {len(knees)} themes:")
    print(
        f"    min={min(knees):.4f} median={statistics.median(knees):.4f} "
        f"mean={statistics.fmean(knees):.4f} max={max(knees):.4f}"
    )
    print("\ncards kept per theme, by cut:")
    for candidate in CANDIDATES:
        counts = kept[candidate]
        name = "no cut" if candidate is None else f"{candidate:.2f}"
        print(
            f"    {name:>7}: median {statistics.median(counts):.1f} "
            f"(min {min(counts)}, max {max(counts)})"
        )


if __name__ == "__main__":
    asyncio.run(main())
