"""The conversation dump's renderer. Pure — no database."""

from scripts.dump_conversation import render_conversation, render_message

STANDING = {
    "search": None,
    "loop": {"kind": "loop_search", "activity": "hike", "max_distance_m": 10000.0},
    "theme": None,
    "routes": [],
}


def test_a_bug_note_becomes_a_flagged_heading():
    lines = render_message("user", "BUG: the routes disappeared", None, None)
    assert lines[0].startswith("### ")
    assert "BUG: the routes disappeared" in lines[0]


def test_a_plain_user_turn_is_not_flagged():
    lines = render_message("user", "a 10 km loop", None, None)
    assert lines == ["**user:** a 10 km loop"]


def test_an_assistant_turn_carries_plan_and_results():
    intent = {"subqueries": [{"kind": "loop_search"}], "standing": STANDING}
    refs = {"loop_ids": ["vv2-aaaa", "vv2-bbbb"]}
    lines = render_message("assistant", "Two loops.", intent, refs)
    assert lines[0] == "**assistant:** Two loops."
    assert any("intents: loop_search" in line for line in lines)
    # The standing plan re-spoken through readback: composer decisions on record.
    assert any("10" in line and "km" in line for line in lines)
    assert any("vv2-aaaa, vv2-bbbb" in line for line in lines)


def test_jsonb_arrives_as_text_and_still_renders():
    import json

    lines = render_message(
        "assistant",
        "ok",
        json.dumps({"subqueries": [{"kind": "trail_search"}]}),
        json.dumps({"trail_ids": ["t1"]}),
    )
    assert any("intents: trail_search" in line for line in lines)
    assert any("t1" in line for line in lines)


def test_malformed_intent_never_breaks_the_dump():
    lines = render_message("assistant", "ok", "{not json", None)
    assert lines[0] == "**assistant:** ok"


def test_conversation_renders_whole():
    text = render_conversation(
        {"title": None, "created_at": "2026-08-26 10:00:00"},
        [
            {"role": "user", "content": "hi", "intent": None, "result_refs": None},
            {
                "role": "assistant",
                "content": "hello",
                "intent": None,
                "result_refs": None,
            },
        ],
    )
    assert text.startswith("## (untitled) — 2026-08-26 10:00:00")
    assert "**user:** hi" in text
