"""Environment-driven settings shared by ingestion, scripts, and the API."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"

    # "minLat,minLon,maxLat,maxLon"
    default_bbox: str = "45.8,9.3,46.0,9.6"
    default_region_name: str = "Lecco"
    # Every region the beta covers: "Name:minLat,minLon,maxLat,maxLon;..."
    # Bergamo's box starts at the city (45.70) and runs north into the hills
    # (Parco dei Colli, Canto Alto, the Brembana/Seriana valley mouths) so the
    # plains' road grid stays out of the graph.
    regions: str = "Lecco:45.8,9.3,46.0,9.6;Bergamo:45.68,9.55,45.92,9.85"

    spatial_match_threshold_m: float = 20.0
    passes_by_threshold_m: float = 50.0
    # Trail-level NEAR_POI edges: a POI within this distance of any trail
    # segment counts as "along the trail" for search filters. 500 m because
    # area features are ingested as a single node (a lake's node sits out on
    # the water, ~400 m from its own shoreline path).
    poi_near_radius_m: float = 500.0
    # How far a catalogue loop's trailhead may sit from a named place before it
    # stops counting as "near" it. Generous, because a walker asking for a loop
    # near a town means the hills above it, not the town square.
    loop_near_radius_m: float = 8000.0

    trailforks_api_key: str = ""
    trailforks_base_url: str = "https://www.trailforks.com/api/1"

    # Internal service, like Neo4j: never published, only the gateway is.
    graphhopper_url: str = "http://localhost:8989"

    overpass_url: str = "https://overpass-api.de/api/interpreter"
    overpass_timeout_s: int = 120

    log_level: str = "info"

    api_host: str = "0.0.0.0"  # noqa: S104 — container-internal, never published
    api_port: int = 8000

    # The backend is not public: every request must carry this shared secret in
    # the X-Gateway-Secret header, proving it came through the Fastify gateway.
    # Empty disables the check — dev/test only, never in a deployed environment.
    gateway_shared_secret: str = ""

    # Routing guardrails
    snap_radius_m: float = 500.0
    max_route_distance_m: float = 100_000.0

    # LLM
    openai_api_key: str = ""
    intent_model: str = "gpt-4o-mini"
    answer_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    daily_token_quota_per_user: int = 50_000

    # Supabase Postgres (chat history, ledger, quotas)
    database_url: str = ""

    @property
    def region_list(self) -> list[tuple[str, tuple[float, float, float, float]]]:
        """[(name, (min_lat, min_lon, max_lat, max_lon)), ...] from REGIONS."""
        out: list[tuple[str, tuple[float, float, float, float]]] = []
        for entry in self.regions.split(";"):
            entry = entry.strip()
            if not entry:
                continue
            name, _, coords = entry.partition(":")
            parts = [float(p) for p in coords.split(",")]
            if not name or len(parts) != 4:
                raise ValueError(
                    "REGIONS entries must be 'Name:minLat,minLon,maxLat,maxLon', "
                    f"got {entry!r}"
                )
            out.append((name.strip(), (parts[0], parts[1], parts[2], parts[3])))
        return out

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """(min_lat, min_lon, max_lat, max_lon)."""
        parts = [float(p) for p in self.default_bbox.split(",")]
        if len(parts) != 4:
            raise ValueError(
                "DEFAULT_BBOX must have 4 comma-separated floats, "
                f"got {self.default_bbox!r}"
            )
        return parts[0], parts[1], parts[2], parts[3]


@lru_cache
def get_settings() -> Settings:
    return Settings()
