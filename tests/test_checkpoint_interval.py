"""Tests for configurable checkpoint interval (#200)."""
import sys
import os
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))


def _make_llm_client(config_dict):
    """Create a minimal mock LLMClient with a .config attribute."""
    client = types.SimpleNamespace(config=config_dict)
    return client


def _read_checkpoint_interval(llm_config):
    """Mirror the logic in extract_semantic_batch."""
    _refresh_cfg = getattr(llm_config, "config", None) or {}
    return int(_refresh_cfg.get("checkpoint_interval", 25)) if isinstance(_refresh_cfg, dict) else 25


def test_checkpoint_interval_defaults_to_25():
    """When checkpoint_interval is absent, default is 25."""
    llm = _make_llm_client({})
    assert _read_checkpoint_interval(llm) == 25


def test_checkpoint_interval_reads_config_value():
    """When checkpoint_interval is set to 10, it reads 10."""
    llm = _make_llm_client({"checkpoint_interval": 10})
    assert _read_checkpoint_interval(llm) == 10


def test_checkpoint_interval_value_in_llm_json():
    """config/llm.json should contain checkpoint_interval = 25."""
    import json
    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "llm.json")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    assert "checkpoint_interval" in config
    assert config["checkpoint_interval"] == 25
