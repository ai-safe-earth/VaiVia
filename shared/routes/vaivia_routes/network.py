"""The routable network: a Pack's edges as one CSR per activity.

Why scipy and not the graph database: every loop, out-and-back and
destination route needs a bounded distance field from the start —
"everything within N km" — dozens of times per ask, and GDS has no cost
cutoff (docs/route-design.md, the measurement table). scipy's
`dijkstra(limit=)` over a CSR of the same arcs does it in tens of
milliseconds and the whole network fits in memory.

The arcs are exactly the pack's cost columns: forward u→v where
`edge_cost_<a>` >= 0, reverse v→u where `edge_cost_<a>_rev` >= 0 — the
same arcs pgRouting saw through `catalogue.v_edges_*`, because the pack
copies those views' costs. A -1 arc does not exist here, so a penalty
can never resurrect it, by construction rather than by CASE expression.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.sparse import csr_array
from scipy.sparse.csgraph import dijkstra

from vaivia_routes.assemble import WalkedEdge
from vaivia_routes.pack import Pack

#: activity -> (forward cost column, reverse cost column)
COST_COLUMNS = {
    "foot": ("edge_cost_foot", "edge_cost_foot_rev"),
    "mtb": ("edge_cost_bike", "edge_cost_bike_rev"),
}

#: The soft penalty for already-walked edges, as in draw.generate: expensive,
#: never illegal — walking back the same valley is sometimes the only way home.
PENALTY_FACTOR = 3.0

METRES_PER_DEG_LAT = 111_320.0


@dataclass(frozen=True)
class Network:
    """One activity's directed graph over a pack, plus the arc→edge map."""

    pack: Pack
    activity: str
    graph: csr_array  # (V, V) float64 weights
    arc_edge: np.ndarray  # per CSR data slot: edge index
    arc_forward: np.ndarray  # per CSR data slot: walked u→v?
    main_component: int
    _cache: dict = field(default_factory=dict, compare=False)

    @classmethod
    def build(cls, pack: Pack, activity: str) -> "Network":
        fwd_col, rev_col = COST_COLUMNS[activity]
        n_v = pack.counts["V"]
        u, v = pack["edge_u"], pack["edge_v"]
        fwd_cost, rev_cost = pack[fwd_col], pack[rev_col]
        fwd_ok = fwd_cost >= 0
        rev_ok = rev_cost >= 0
        edge_index = np.arange(len(u))
        rows = np.concatenate((u[fwd_ok], v[rev_ok]))
        cols = np.concatenate((v[fwd_ok], u[rev_ok]))
        data = np.concatenate((fwd_cost[fwd_ok], rev_cost[rev_ok])).astype(np.float64)
        arc_edge = np.concatenate((edge_index[fwd_ok], edge_index[rev_ok]))
        arc_forward = np.concatenate(
            (np.ones(fwd_ok.sum(), bool), np.zeros(rev_ok.sum(), bool))
        )
        # Parallel arcs between the same pair survive: csr_array would SUM
        # duplicate entries, which is a wrong graph, so the triplets are kept
        # as-is via coo and duplicates resolved by dijkstra taking the min
        # path anyway — but scipy's coo→csr conversion sums too. Keep only
        # the cheapest arc per (row, col); the walked-sequence reconstruction
        # re-reads the survivors, so the dropped arc can never be reported.
        order = np.lexsort((data, cols, rows))
        rows, cols, data = rows[order], cols[order], data[order]
        arc_edge, arc_forward = arc_edge[order], arc_forward[order]
        keep = np.concatenate(
            ([True], (rows[1:] != rows[:-1]) | (cols[1:] != cols[:-1]))
        )
        graph = csr_array((data[keep], (rows[keep], cols[keep])), shape=(n_v, n_v))
        component = pack["vertex_component"]
        counts = np.bincount(component - component.min())
        main = int(counts.argmax() + component.min())
        return cls(
            pack=pack,
            activity=activity,
            graph=graph,
            arc_edge=arc_edge[keep],
            arc_forward=arc_forward[keep],
            main_component=main,
        )

    # -- queries ---------------------------------------------------------

    def field_from(self, source: int, limit: float) -> np.ndarray:
        """Cost to every vertex within `limit`, inf beyond — the bounded
        distance field the shapes are drawn from."""
        return dijkstra(self.graph, indices=source, limit=limit)

    def route(
        self,
        source: int,
        target: int,
        penalised: set[int] | frozenset[int] = frozenset(),
        factor: float = PENALTY_FACTOR,
        two_way_only: bool = False,
    ) -> list[tuple[int, bool]] | None:
        """Cheapest source→target walk as [(edge index, forward)], or None.

        `penalised` multiplies both directions of those edges' costs by
        `factor` (a soft penalty, as in draw.generate). `two_way_only`
        restricts to edges legal in BOTH directions — what a strict
        out-and-back needs so the way home is legal (draw.generate's
        `draw_strict_out_and_back` filter).
        """
        graph = self.graph
        if penalised or two_way_only:
            data = graph.data.copy()
            if penalised:
                mask = np.isin(self.arc_edge, np.fromiter(penalised, dtype=np.int64))
                data[mask] *= factor
            if two_way_only:
                fwd_col, rev_col = COST_COLUMNS[self.activity]
                both = (self.pack[fwd_col] >= 0) & (self.pack[rev_col] >= 0)
                data[~both[self.arc_edge]] = np.inf
            graph = csr_array((data, graph.indices, graph.indptr), shape=graph.shape)
        dist, predecessors = dijkstra(graph, indices=source, return_predecessors=True)
        if target != source and (
            predecessors[target] < 0 or not np.isfinite(dist[target])
        ):
            return None
        path = [target]
        while path[-1] != source:
            path.append(int(predecessors[path[-1]]))
        path.reverse()
        return [self._arc(graph, a, b) for a, b in zip(path, path[1:])]

    def _arc(self, graph: csr_array, a: int, b: int) -> tuple[int, bool]:
        """The (edge index, forward) the CSR keeps for the arc a→b."""
        lo, hi = graph.indptr[a], graph.indptr[a + 1]
        slot = lo + int(np.searchsorted(graph.indices[lo:hi], b))
        return int(self.arc_edge[slot]), bool(self.arc_forward[slot])

    def nearest_vertex(self, lon: float, lat: float) -> int:
        """The nearest main-component vertex, by local equirectangular
        distance — the same metric that placed the via rings."""
        vlon, vlat = self.pack["vertex_lon"], self.pack["vertex_lat"]
        on_main = self.pack["vertex_component"] == self.main_component
        kx = METRES_PER_DEG_LAT * math.cos(math.radians(lat))
        d2 = (kx * (vlon - lon)) ** 2 + (METRES_PER_DEG_LAT * (vlat - lat)) ** 2
        d2 = np.where(on_main, d2, np.inf)
        return int(d2.argmin())

    # -- hydration -------------------------------------------------------

    def walked(self, steps: list[tuple[int, bool]]) -> list[WalkedEdge]:
        """(edge index, forward) steps as WalkedEdges, decoded from the pack."""
        p = self.pack
        tables = self._cache.setdefault(
            "decoded",
            {
                name: p.decode(name)
                for name in (
                    "edge_surface",
                    "edge_sac_scale",
                    "edge_mtb_scale",
                    "edge_highway",
                )
            },
        )

        def opt(x: float) -> float | None:
            return None if math.isnan(x) else float(x)

        out = []
        for i, forward in steps:
            profile = p.edge_profile(i)
            out.append(
                WalkedEdge(
                    edge_id=int(p["edge_id"][i]),
                    forward=forward,
                    length_m=float(p["edge_length_m"][i]),
                    coords=[(float(x), float(y)) for x, y in p.edge_geometry(i)],
                    profile_m=(
                        None if np.isnan(profile).any() else [float(z) for z in profile]
                    ),
                    ascent_m=opt(p["edge_ascent_m"][i]),
                    descent_m=opt(p["edge_descent_m"][i]),
                    surface=tables["edge_surface"][i],
                    sac_scale=tables["edge_sac_scale"][i],
                    mtb_scale=tables["edge_mtb_scale"][i],
                    highway=tables["edge_highway"][i],
                    routable_bike=bool(p["edge_routable_bike"][i]),
                    urban_m=opt(p["edge_urban_m"][i]),
                    source=int(p["edge_u"][i]),
                    target=int(p["edge_v"][i]),
                )
            )
        return out
