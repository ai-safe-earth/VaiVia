"""The pack: the cleaned network as numpy arrays, one directory per build.

The network is the catalogue (docs/route-design.md). The pipeline writes one
pack per build — `network.npz` beside a `manifest.json` — and the backend loads
it and draws routes over it at ask time. This module is the format's single
definition: what arrays exist, their dtypes, how their lengths relate, and the
invariants a pack must hold to be loaded at all. A pack that fails to load is
better than one that routes wrong.

Layout. Vertices, edges and places are three tables indexed 0..n-1; an edge's
`u`/`v` and a place's `vertex` are indices into the vertex table, never
`vertex_id`s (those travel in `vertex_id`, for Neo4j and QGIS). Edge geometry
is ragged: `geom_offsets[i]:geom_offsets[i+1]` slices one edge's points out of
`geom_lon`/`geom_lat`/`geom_ele` — the profile is one sample per point, as
`source_map.edge.profile_m` stores it. Every string column is a small-integer
code with its table in `manifest["codes"]`; -1 is NULL. A directional cost of
-1 means the arc may not be travelled that way (pgRouting's convention, kept
so the cost columns read the same as `catalogue.v_edges_*`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

FORMAT = 1
NETWORK = "network.npz"
MANIFEST = "manifest.json"

#: Length symbols. Every array's length is one of these, read from
#: `manifest["counts"]`; `+1` marks an offsets array.
COUNTS = ("V", "E", "P", "K")

#: name -> (dtype, length). "U" is any fixed-width unicode dtype.
SPEC: dict[str, tuple[str, str]] = {
    # vertices
    "vertex_id": ("int64", "V"),
    "vertex_lon": ("float64", "V"),
    "vertex_lat": ("float64", "V"),
    "vertex_component": ("int64", "V"),
    # edges, attributes stored along the edge's own direction (u -> v)
    "edge_id": ("int64", "E"),
    "edge_u": ("int32", "E"),
    "edge_v": ("int32", "E"),
    "edge_length_m": ("float32", "E"),
    "edge_ascent_m": ("float32", "E"),  # NaN unknown
    "edge_descent_m": ("float32", "E"),
    "edge_urban_m": ("float32", "E"),
    "edge_cost_foot": ("float32", "E"),  # u -> v; -1 forbidden
    "edge_cost_foot_rev": ("float32", "E"),  # v -> u
    "edge_cost_bike": ("float32", "E"),
    "edge_cost_bike_rev": ("float32", "E"),
    "edge_surface": ("int16", "E"),
    "edge_highway": ("int16", "E"),
    "edge_sac_scale": ("int16", "E"),
    "edge_mtb_scale": ("int16", "E"),
    "edge_routable_bike": ("bool", "E"),
    # ragged geometry, profile per point
    "geom_offsets": ("int64", "E+1"),
    "geom_lon": ("float64", "P"),
    "geom_lat": ("float64", "P"),
    "geom_ele": ("float32", "P"),  # NaN where the DEM had no sample
    # places, every source_map.place row; starts are the is_start mask
    "place_vertex": ("int32", "K"),
    "place_lon": ("float64", "K"),
    "place_lat": ("float64", "K"),
    "place_source": ("int16", "K"),
    "place_kind": ("int16", "K"),
    "place_name": ("U", "K"),  # "" unnamed
    "place_source_id": ("U", "K"),
    "place_ele_m": ("float32", "K"),  # NaN unknown
    "place_distance_m": ("float32", "K"),  # feature to its vertex
    "place_is_start": ("bool", "K"),
    "place_start_class": ("int16", "K"),
    "place_n_trips": ("int32", "K"),  # -1 not a stop
    "place_service_start": ("int32", "K"),  # days since 1970-01-01; -1 none
    "place_service_end": ("int32", "K"),
}

#: The string columns and the manifest table each decodes through.
CODE_TABLES: dict[str, str] = {
    "edge_surface": "surface",
    "edge_highway": "highway",
    "edge_sac_scale": "sac_scale",
    "edge_mtb_scale": "mtb_scale",
    "place_source": "place_source",
    "place_kind": "place_kind",
    "place_start_class": "start_class",
}

COST_COLUMNS = (
    "edge_cost_foot",
    "edge_cost_foot_rev",
    "edge_cost_bike",
    "edge_cost_bike_rev",
)


class PackError(ValueError):
    """The pack does not hold the format's invariants."""


@dataclass(frozen=True)
class Pack:
    manifest: dict[str, Any]
    arrays: dict[str, np.ndarray]

    def __getitem__(self, name: str) -> np.ndarray:
        return self.arrays[name]

    @property
    def run_id(self) -> str:
        return self.manifest["run_id"]

    @property
    def counts(self) -> dict[str, int]:
        return self.manifest["counts"]

    def decode(self, name: str) -> list[str | None]:
        """A coded column as its strings, None for -1."""
        table = self.manifest["codes"][CODE_TABLES[name]]
        return [None if c < 0 else table[c] for c in self.arrays[name].tolist()]

    def edge_geometry(self, i: int) -> np.ndarray:
        """One edge's points as an (n, 2) lon/lat array, u -> v."""
        lo, hi = self.arrays["geom_offsets"][i : i + 2]
        return np.column_stack(
            (self.arrays["geom_lon"][lo:hi], self.arrays["geom_lat"][lo:hi])
        )

    def edge_profile(self, i: int) -> np.ndarray:
        """Elevation per point of `edge_geometry(i)`, NaN where unknown."""
        lo, hi = self.arrays["geom_offsets"][i : i + 2]
        return self.arrays["geom_ele"][lo:hi]


def encode(values: list[str | None]) -> tuple[np.ndarray, list[str]]:
    """Strings to int16 codes over a sorted table; None is -1."""
    table = sorted({v for v in values if v is not None})
    index = {v: i for i, v in enumerate(table)}
    codes = np.array([-1 if v is None else index[v] for v in values], dtype=np.int16)
    return codes, table


def ragged(rows: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Variable-length rows as (offsets[n+1], flat)."""
    lengths = np.array([len(r) for r in rows], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(lengths)))
    flat = np.concatenate(rows) if rows else np.array([], dtype=np.float64)
    return offsets, flat


def validate(arrays: dict[str, np.ndarray], manifest: dict[str, Any]) -> None:
    """Every array the spec names, no other, at its dtype and length — and the
    cross-table invariants a router would otherwise trip over silently."""
    if manifest.get("format") != FORMAT:
        raise PackError(f"format {manifest.get('format')!r}, this reader is {FORMAT}")
    for key in ("run_id", "counts", "codes"):
        if key not in manifest:
            raise PackError(f"manifest lacks {key!r}")
    counts = manifest["counts"]
    missing = [c for c in COUNTS if c not in counts]
    if missing:
        raise PackError(f"manifest counts lack {missing}")

    unknown = sorted(set(arrays) - set(SPEC))
    if unknown:
        raise PackError(f"arrays not in the spec: {unknown}")
    for name, (dtype, length) in SPEC.items():
        if name not in arrays:
            raise PackError(f"array missing: {name}")
        a = arrays[name]
        if dtype == "U":
            if a.dtype.kind != "U":
                raise PackError(f"{name}: dtype {a.dtype}, wanted unicode")
        elif a.dtype != np.dtype(dtype):
            raise PackError(f"{name}: dtype {a.dtype}, wanted {dtype}")
        want = counts[length[0]] + (1 if length.endswith("+1") else 0)
        if a.ndim != 1 or len(a) != want:
            raise PackError(f"{name}: shape {a.shape}, wanted ({want},)")

    for name, table in CODE_TABLES.items():
        if table not in manifest["codes"]:
            raise PackError(f"manifest codes lack {table!r}")
        codes = arrays[name]
        if len(codes) and (
            codes.min() < -1 or codes.max() >= len(manifest["codes"][table])
        ):
            raise PackError(f"{name}: a code outside its table {table!r}")

    n_v, n_e, n_p = (counts[c] for c in COUNTS[:3])
    for name in ("edge_u", "edge_v", "place_vertex"):
        a = arrays[name]
        if len(a) and (a.min() < 0 or a.max() >= n_v):
            raise PackError(f"{name}: an index outside the vertex table")
    if len(arrays["edge_u"]) and (arrays["edge_u"] == arrays["edge_v"]).any():
        raise PackError("an edge with u == v")
    off = arrays["geom_offsets"]
    if off[0] != 0 or off[-1] != n_p or (np.diff(off) < 0).any():
        raise PackError(f"geom_offsets: not a monotone offsets array ending at {n_p}")
    if n_e and (np.diff(off) < 2).any():
        raise PackError("an edge with fewer than two points")
    for name in COST_COLUMNS:
        cost = arrays[name]
        legal = cost[cost != -1]
        if len(legal) and (~(legal > 0)).any():
            raise PackError(f"{name}: a cost that is neither -1 nor positive")
    if len(arrays["vertex_id"]) and len(np.unique(arrays["vertex_id"])) != n_v:
        raise PackError("vertex_id is not unique")
    if len(arrays["edge_id"]) and len(np.unique(arrays["edge_id"])) != n_e:
        raise PackError("edge_id is not unique")


def write(
    directory: Path, arrays: dict[str, np.ndarray], manifest: dict[str, Any]
) -> Path:
    """Validate, then write `network.npz` + `manifest.json` into `directory`."""
    manifest = {**manifest, "format": FORMAT}
    validate(arrays, manifest)
    directory.mkdir(parents=True, exist_ok=True)
    np.savez(directory / NETWORK, **arrays)
    (directory / MANIFEST).write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return directory


def load(directory: Path) -> Pack:
    """Read and validate a pack. Arrays are materialised (35 MB for the two
    provinces); a fork-based worker pool shares them copy-on-write."""
    manifest = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
    with np.load(directory / NETWORK) as npz:
        arrays = {name: npz[name] for name in npz.files}
    validate(arrays, manifest)
    return Pack(manifest=manifest, arrays=arrays)
