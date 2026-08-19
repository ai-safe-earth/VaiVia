"""Settings resolution: the pipeline finds its database without a live server."""

import core


def test_env_var_wins(monkeypatch) -> None:
    monkeypatch.setenv("PIPELINE_DATABASE_URL", "postgresql://x:y@127.0.0.1:5433/z")
    assert core.database_url() == "postgresql://x:y@127.0.0.1:5433/z"


def test_derived_from_postgis_vars(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("PIPELINE_DATABASE_URL", raising=False)
    (tmp_path / ".env").write_text(
        "POSTGIS_PASSWORD=pw\nPOSTGIS_PORT=5433\n# comment=ignored\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(core, "REPO_ROOT", tmp_path)
    assert core.database_url() == "postgresql://vaivia:pw@127.0.0.1:5433/vaivia_geo"


def test_regions_match_backend() -> None:
    # The pipeline and backend must agree on what the two provinces are; a
    # drifted bbox silently builds a catalogue for a different place.
    assert core.REGIONS["Lecco"] == (45.8, 9.3, 46.0, 9.6)
    assert core.REGIONS["Bergamo"] == (45.68, 9.55, 45.92, 9.85)


def test_crs_pair() -> None:
    assert core.CRS_STORAGE == 4326
    assert core.CRS_METRIC == 32632
