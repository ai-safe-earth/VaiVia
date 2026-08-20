"""Provider spike: can other sources give us routes, and what do they add?

The question (owner, 2026-08-20): test the same methodology against OSM,
OpenRouteService, TrailSplits and FreeRoute — get the data; if it is routes,
enrich; if it is segments, draw routes first; add POIs and the route↔POI
relationship — and find the wisest combination of sources for a map of routes
with difficulty and an MTB verdict.

The answer this spike is built to produce is a MEASURED one: every provider's
candidate routes go through the same enrichment (spike_providers/enrich.py)
and come out as the same route document, so the comparison is between documents
that differ only in where their geometry came from.

Spike, not product: lives on spike/route-providers, writes only under
review/spike-providers/ and pipeline/data/spike_cache/, touches nothing in
curated. The findings doc is pipeline/docs/provider-comparison.md.
"""
