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

    spatial_match_threshold_m: float = 20.0
    passes_by_threshold_m: float = 50.0

    trailforks_api_key: str = ""
    trailforks_base_url: str = "https://www.trailforks.com/api/1"

    overpass_url: str = "https://overpass-api.de/api/interpreter"
    overpass_timeout_s: int = 120

    log_level: str = "info"

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
