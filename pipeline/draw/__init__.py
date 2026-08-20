"""Route generation: draw bounded loop candidates over the curated network.

The design docs/route-pipeline.md ratified — anchor × distance × seed →
generate, score, keep the best few — built where the data now lives, over
curated.edge with pgRouting. The provider spike settled the engine question
(pipeline/docs/provider-comparison.md): routing over our own edges makes every
route an edge SEQUENCE, so difficulty, the MTB conjunction and ascent are read
along it natively, with no corridor match.

Layout:
    route_id.py   the geometry-derived stable id (docs/social-layer.md imposes it)
    assemble.py   pure: an edge sequence -> measures, difficulty, MTB, warnings
    loops.py      pure: via-point rings, dedupe, scoring
    generate.py   the CLI: pgr_dijkstra legs, candidates, curated.route
"""
