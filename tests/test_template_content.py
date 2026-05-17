"""Tests for extraction template content quality gates."""
import os
import pytest

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "..", "templates", "extraction")


def _load_template(name):
    with open(os.path.join(TEMPLATE_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


class TestRelationshipMapperTemplate:
    def test_has_kinship_section(self):
        content = _load_template("relationship-mapper.md")
        assert "KINSHIP RELATIONSHIPS" in content

    def test_kinship_has_examples(self):
        content = _load_template("relationship-mapper.md")
        assert "kinship" in content.lower()
        assert "parent" in content.lower() or "spouse" in content.lower()


class TestEntityDiscoveryTemplate:
    def test_has_phantom_prevention(self):
        content = _load_template("entity-discovery.md")
        assert "ENTITY NAME VALIDATION" in content

    def test_compound_term_warning(self):
        content = _load_template("entity-discovery.md")
        assert "compound term" in content.lower() or "SUBSTRING" in content
