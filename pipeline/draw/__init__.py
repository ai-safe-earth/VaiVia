"""What is left of the catalogue generator: divergence.py.

Drawing moved to the shared package (vaivia_routes: draw, assemble, loops,
destinations, ids) in R2, and the pgRouting CLI went with the catalogue in R7
(docs/route-design.md). `divergence` measures where sibling routes from one
terminal part ways (start/end contract §6) and is read by the route document
emitter.
"""
