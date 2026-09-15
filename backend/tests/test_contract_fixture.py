"""The pipeline's contract fixture, served through the real document reader.

pipeline/emit_contract_fixture.py wrote backend/fixtures/
route_document_contract.json and the pipeline suite pins it byte-identical
to the emitter. This side serves that SAME file through /routes/{id}/geojson
and /detail, so a schema change that breaks a read path fails a build here
instead of 500ing in production. CI stays offline for both suites.
"""

import json
import shutil
from pathlib import Path

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "route_document_contract.json"
)


def _mount(tmp_path, monkeypatch):
    from core.config import get_settings

    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    monkeypatch.setattr(get_settings(), "route_documents_dir", str(tmp_path))
    shutil.copy(FIXTURE, tmp_path / f"{document['id']}.json")
    return document


def test_the_contract_document_serves_geojson_whole(client, db, tmp_path, monkeypatch):
    document = _mount(tmp_path, monkeypatch)
    db.when(
        "route_exists",
        [{"id": document["id"], "doc_run_id": document["provenance"]["run_id"]}],
    )
    body = client.get(f"/routes/{document['id']}/geojson").json()
    assert body["geometry"]["coordinates"] == document["geometry"]["coordinates"]
    assert body["properties"]["route_id"] == document["id"]
    assert body["properties"]["kind"] == document["kind"]
    assert body["properties"]["shape"] == document["shape"]
    assert "OpenStreetMap" in body["properties"]["attribution"]


def test_the_contract_document_serves_detail_whole(client, db, tmp_path, monkeypatch):
    document = _mount(tmp_path, monkeypatch)
    db.when(
        "route_exists",
        [{"id": document["id"], "doc_run_id": document["provenance"]["run_id"]}],
    )
    body = client.get(f"/routes/{document['id']}/detail").json()
    assert body["route_id"] == document["id"]
    assert body["measures"] == document["measures"]
    assert body["continuity"]["pieces"] == document["continuity"]["pieces"]
    assert body["surface"]["dominant"] == document["surface"]["dominant"]
    assert body["profile"] == document["profile"]
    assert [p["name"] for p in body["places"]] == [
        p["name"] for p in document["places"]
    ]
