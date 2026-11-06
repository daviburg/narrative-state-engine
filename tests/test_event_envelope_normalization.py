import copy
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import semantic_extraction as se


@pytest.fixture
def event():
    return {
        "id": "evt-001", "source_turns": ["turn-001"], "type": "discovery",
        "description": "The player discovered a marked trail.",
        "related_entities": ["char-player"],
    }


def test_valid_single_event_is_wrapped_without_fact_changes(event):
    se._reset_validation_repairs()
    original = copy.deepcopy(event)
    assert se._normalize_event_response(event, "turn-001") == [original]
    assert event == original
    repairs = se._drain_validation_repairs()
    assert len(repairs) == 1
    assert repairs[0]["phase"] == "event"
    assert repairs[0]["repair"] == "wrap_single_event"
    assert repairs[0]["event_id"] == "evt-001"


def test_wrapped_array_and_empty_result_are_preserved(event):
    se._reset_validation_repairs()
    events = [event]
    assert se._normalize_event_response({"events": events}, "turn-001") is events
    assert se._normalize_event_response({"events": []}, "turn-001") == []
    assert se._drain_validation_repairs() == []


@pytest.mark.parametrize("result", [{}, {"events": None}, {"events": {}},
                                   {"events": ["invalid"]}, {"id": "evt-001"}])
def test_invalid_response_does_not_become_silent_empty_success(result):
    with pytest.raises(se.LLMExtractionError):
        se._normalize_event_response(result, "turn-001")


def test_union_schema_accepts_valid_event_or_envelope_and_rejects_invalid(event):
    schema = se._event_response_schema()
    se.jsonschema.validate(event, schema)
    se.jsonschema.validate({"events": [event]}, schema)
    se.jsonschema.validate({"events": []}, schema)
    for invalid in ({}, {"events": None}, {"id": "not-valid"}):
        with pytest.raises(se.jsonschema.ValidationError):
            se.jsonschema.validate(invalid, schema)


def test_parallel_event_call_uses_same_normalizer_and_local_validation(event):
    se._reset_validation_repairs()
    llm = MagicMock()
    llm.extract_json.return_value = event
    result = se._extract_events_parallel(llm, {"turn_id": "turn-001", "speaker": "dm", "text": "A marked trail."},
                                         1, ["char-player"])
    assert result == [event]
    assert "schema" in llm.extract_json.call_args.kwargs
    assert se._drain_validation_repairs()[0]["repair"] == "wrap_single_event"


def test_single_event_cannot_be_salvaged_without_schema_validation(event, monkeypatch):
    monkeypatch.setattr(se, "_load_schema", lambda name: None)
    with pytest.raises(se.LLMExtractionError):
        se._normalize_event_response(event, "turn-001")


@pytest.mark.parametrize("parallel_workers", [1, 4])
def test_both_pipeline_paths_retain_and_log_single_event(event, monkeypatch, parallel_workers):
    from test_parallel_extraction import _fresh_catalogs, _make_parallel_llm
    monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
    se._reset_pc_failure_tracking()
    llm = _make_parallel_llm(parallel_workers=parallel_workers)
    original = llm.extract_json.side_effect

    def response(*args, **kwargs):
        prompt = kwargs.get("system_prompt", args[0] if args else "")
        if "event-extractor" in prompt:
            return copy.deepcopy(event)
        return original(*args, **kwargs)

    llm.extract_json.side_effect = response
    _, events, failed, log = se.extract_and_merge(
        {"turn_id": "turn-001", "speaker": "dm", "text": "The player discovered a marked trail."},
        _fresh_catalogs(), [], llm, min_confidence=0.6,
    )
    assert not failed
    assert any(value["id"] == event["id"] for value in events)
    assert any(repair["repair"] == "wrap_single_event" and repair["phase"] == "event"
               for repair in log["validation_repairs"])
