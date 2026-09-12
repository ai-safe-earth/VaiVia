"""The committed contract fixture IS what the emitter produces — byte for byte.

backend/fixtures/route_document_contract.json is served by the backend suite
through its real document reader; this side pins that the file is exactly
what build_document emits from the canonical inputs, and that it satisfies
the schema. Either tier drifting fails a build, not production.
"""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from emit_contract_fixture import OUT, contract_document

SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "route-document.schema.json"


def test_the_committed_fixture_is_byte_identical_to_the_emitter():
    committed = OUT.read_text(encoding="utf-8")
    rebuilt = json.dumps(contract_document(), indent=2, ensure_ascii=False) + "\n"
    assert committed == rebuilt, (
        "backend/fixtures/route_document_contract.json no longer matches "
        "build_document — re-run `uv run python emit_contract_fixture.py` "
        "and commit BOTH sides"
    )


def test_the_fixture_satisfies_the_schema():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(json.loads(OUT.read_text(encoding="utf-8")))
