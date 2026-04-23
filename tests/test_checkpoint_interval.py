"""Tests for configurable checkpoint interval (#200)."""
import sys
import os
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import _read_checkpoint_interval


def _make_llm_client(config_dict):
    """Create a minimal mock LLMClient with a .config attribute."""
    client = types.SimpleNamespace(config=config_dict)
    return client


def test_checkpoint_interval_defaults_to_25():
    """When checkpoint_interval is absent, default is 25."""
    assert _read_checkpoint_interval({}) == 25


def test_checkpoint_interval_reads_config_value():
    """When checkpoint_interval is set to 10, it reads 10."""
    assert _read_checkpoint_interval({"checkpoint_interval": 10}) == 10


def test_checkpoint_interval_invalid_string_falls_back_to_default():
    """Non-numeric string falls back to default 25."""
    assert _read_checkpoint_interval({"checkpoint_interval": "bad"}) == 25


def test_checkpoint_interval_zero_falls_back_to_default():
    """Zero is invalid (<1) and falls back to default 25."""
    assert _read_checkpoint_interval({"checkpoint_interval": 0}) == 25


def test_checkpoint_interval_negative_falls_back_to_default():
    """Negative value falls back to default 25."""
    assert _read_checkpoint_interval({"checkpoint_interval": -5}) == 25


def test_checkpoint_interval_value_in_llm_json():
    """config/llm.json should contain checkpoint_interval = 25."""
    import json
    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "llm.json")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    assert "checkpoint_interval" in config
    assert config["checkpoint_interval"] == 25
