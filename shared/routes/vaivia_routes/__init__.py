"""The route engine both tiers run (docs/route-design.md).

Slice 1 holds the pack format only. Assembly, loops, destinations, the document
builder and the id mint move here from `pipeline/draw` and `pipeline/ids.py` in
slice 2, so that a route drawn by the pipeline and one drawn at ask time are
the same code.
"""
