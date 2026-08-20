"""Shared plumbing for the provider spike: HTTP with an on-disk cache, and the
one shape every provider is normalised into.

The cache is not an optimisation. It is what makes the spike re-runnable and
polite: every response is kept under pipeline/data/spike_cache/ (gitignored),
so iterating on the enrichment never re-hits a provider, and the exact bytes a
finding was based on are still on disk when the finding is questioned.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "spike_cache"

# Identify ourselves honestly on every request: who, and why.
USER_AGENT = "VaiVia-provider-spike/0.1 (source evaluation; arroscar@gmail.com)"

# The Lecco working bbox from core.REGIONS, the area every provider is asked
# about so the comparison is like-for-like.
LECCO = (45.8, 9.3, 46.0, 9.6)  # min_lat, min_lon, max_lat, max_lon


@dataclass
class Candidate:
    """One candidate route from some provider, before enrichment.

    Geometry is a list of (lon, lat) paths — one for a continuous line, several
    when the provider hands the route over in pieces. Everything else is
    whatever identity the provider offered; enrichment adds the rest.
    """

    provider: str
    candidate_id: str
    name: str | None
    paths: list[list[tuple[float, float]]]
    activity: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def points(self) -> int:
        return sum(len(p) for p in self.paths)


def fetch(
    url: str,
    *,
    method: str = "GET",
    params: dict | None = None,
    json_body: dict | None = None,
    headers: dict | None = None,
    timeout: float = 60.0,
) -> tuple[int, str]:
    """(status, body) with an on-disk cache keyed on the full request.

    HTTP errors are cached too — a provider that 500s is a finding, and
    re-asking on every iteration would be noise for them. TRANSPORT errors are
    NOT cached: a timeout is weather, not a finding, and caching one froze a
    transient failure into "trails/v1/bbox returned 0" on the first live run.
    Delete pipeline/data/spike_cache/ to re-ask everything.
    """
    key_src = json.dumps(
        {"m": method, "u": url, "p": params, "b": json_body}, sort_keys=True
    )
    key = hashlib.sha256(key_src.encode()).hexdigest()[:24]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{key}.json"
    if cache.is_file():
        entry = json.loads(cache.read_text(encoding="utf-8"))
        return entry["status"], entry["body"]

    request_headers = {"User-Agent": USER_AGENT} | (headers or {})
    try:
        response = httpx.request(
            method,
            url,
            params=params,
            json=json_body,
            headers=request_headers,
            timeout=timeout,
            follow_redirects=True,
        )
        status, body = response.status_code, response.text
    except httpx.HTTPError as error:
        status, body = 0, f"transport error: {error}"

    if status > 0:
        cache.write_text(
            json.dumps(
                {"status": status, "url": url, "body": body}, ensure_ascii=False
            ),
            encoding="utf-8",
        )
    return status, body


def geojson_paths(geometry: dict) -> list[list[tuple[float, float]]]:
    """A GeoJSON LineString or MultiLineString as the Candidate path shape."""
    if geometry["type"] == "LineString":
        return [[(x, y) for x, y, *_ in geometry["coordinates"]]]
    if geometry["type"] == "MultiLineString":
        return [[(x, y) for x, y, *_ in part] for part in geometry["coordinates"]]
    raise ValueError(f"not a line geometry: {geometry['type']}")
