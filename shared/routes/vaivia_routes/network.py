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

import itertools
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
    """One activity's directed graph over a pack, plus the arc→edge map.

    The pack is a multigraph — parallel edges between the same vertex pair
    are real (two paths along the same valley) — and scipy's CSR is not:
    coo→csr conversion SUMS duplicate entries. So the full arc list is kept
    sorted by (row, col), and a CSR is reduced from it by taking the
    cheapest arc per pair AFTER penalties are applied. Reducing before
    penalising was measured wrong on the catalogue: the ×3 penalty on a
    walked arc is exactly what makes its dropped parallel the right way
    home, and pgRouting (all arcs) took it where the reduced graph could
    not — 22 of 627 asks drew a different route.
    """

    pack: Pack
    activity: str
    # the full arc list, lexsorted by (row, col, cost)
    rows: np.ndarray
    cols: np.ndarray
    data: np.ndarray
    arc_edge: np.ndarray  # per arc: edge index
    arc_forward: np.ndarray  # per arc: walked u→v?
    graph: csr_array  # the unpenalised reduction, for fields
    graph_arc: np.ndarray  # per CSR slot: index into the arc list
    arc_group: np.ndarray  # per arc: its (row, col) pair = its CSR slot
    pair_start: np.ndarray  # first arc index per pair, len n_pairs + 1
    main_component: int
    _cache: dict = field(default_factory=dict, compare=False)

    @classmethod
    def build(cls, pack: Pack, activity: str) -> Network:
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
        order = np.lexsort((data, cols, rows))
        rows, cols, data = rows[order], cols[order], data[order]
        # Arcs are (row, col, cost)-sorted, so each pair's FIRST arc is its
        # cheapest and the reduction is just the pair-start slice. The pair
        # index doubles as the CSR slot: pairs and CSR entries share the
        # (row, col) order.
        new_pair = np.concatenate(
            ([True], (rows[1:] != rows[:-1]) | (cols[1:] != cols[:-1]))
        )
        arc_group = np.cumsum(new_pair) - 1
        pair_start = np.concatenate((np.flatnonzero(new_pair), [len(rows)]))
        winners = pair_start[:-1]
        graph = csr_array(
            (data[winners], (rows[winners], cols[winners])), shape=(n_v, n_v)
        )
        component = pack["vertex_component"]
        counts = np.bincount(component - component.min())
        main = int(counts.argmax() + component.min())
        return cls(
            pack=pack,
            activity=activity,
            rows=rows,
            cols=cols,
            data=data,
            arc_edge=arc_edge[order],
            arc_forward=arc_forward[order],
            graph=graph,
            graph_arc=winners,
            arc_group=arc_group,
            pair_start=pair_start,
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
        graph, graph_arc = self.graph, self.graph_arc
        if penalised or two_way_only:
            # Re-reduce only the pairs an adjusted arc belongs to — the
            # rest of the CSR keeps its cheapest arc as built.
            affected = np.zeros(len(self.data), dtype=bool)
            if penalised:
                affected |= np.isin(
                    self.arc_edge, np.fromiter(penalised, dtype=np.int64)
                )
            banned = None
            if two_way_only:
                fwd_col, rev_col = COST_COLUMNS[self.activity]
                both = (self.pack[fwd_col] >= 0) & (self.pack[rev_col] >= 0)
                banned = ~both[self.arc_edge]
                affected |= banned
            gdata = graph.data.copy()
            garc = graph_arc.copy()
            for pair in np.unique(self.arc_group[affected]):
                lo, hi = self.pair_start[pair], self.pair_start[pair + 1]
                costs = self.data[lo:hi].copy()
                if penalised:
                    for k in range(hi - lo):
                        if self.arc_edge[lo + k] in penalised:
                            costs[k] *= factor
                if banned is not None:
                    costs[banned[lo:hi]] = np.inf
                k = int(costs.argmin())
                gdata[pair], garc[pair] = costs[k], lo + k
            graph = csr_array((gdata, graph.indices, graph.indptr), shape=graph.shape)
            graph_arc = garc
        dist, predecessors = dijkstra(graph, indices=source, return_predecessors=True)
        if target != source and (
            predecessors[target] < 0 or not np.isfinite(dist[target])
        ):
            return None
        path = [target]
        while path[-1] != source:
            path.append(int(predecessors[path[-1]]))
        path.reverse()
        steps = []
        for a, b in itertools.pairwise(path):
            lo, hi = graph.indptr[a], graph.indptr[a + 1]
            slot = lo + int(np.searchsorted(graph.indices[lo:hi], b))
            arc = graph_arc[slot]
            steps.append((int(self.arc_edge[arc]), bool(self.arc_forward[arc])))
        return steps

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
