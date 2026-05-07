"""Tests for entity type classification filters (#303)."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import _is_misclassified_character, _is_misclassified_location


class TestMisclassifiedCharacterRejected:
    def test_plague_rejected(self):
        assert _is_misclassified_character({"name": "plague", "type": "character"})

    def test_method_rejected(self):
        assert _is_misclassified_character({"name": "method", "type": "character"})

    def test_birth_rejected(self):
        assert _is_misclassified_character({"name": "birth", "type": "character"})

    def test_the_sickness_rejected(self):
        assert _is_misclassified_character({"name": "the sickness", "type": "character"})

    def test_the_feast_rejected(self):
        assert _is_misclassified_character({"name": "the feast", "type": "character"})

    def test_communal_meal_rejected(self):
        assert _is_misclassified_character({"name": "Communal Meal", "type": "character"})


class TestMyBodypartRejected:
    def test_my_belly_rejected(self):
        assert _is_misclassified_character({"name": "my belly", "type": "character"})

    def test_my_arm_rejected(self):
        assert _is_misclassified_character({"name": "my arm", "type": "character"})


class TestLowercaseSingleWordRejected:
    def test_disruption_rejected(self):
        assert _is_misclassified_character({"name": "disruption", "type": "character"})

    def test_precision_rejected(self):
        assert _is_misclassified_character({"name": "precision", "type": "character"})


class TestProperNameNotRejected:
    def test_kael_not_rejected(self):
        assert not _is_misclassified_character({"name": "Kael", "type": "character"})

    def test_theron_not_rejected(self):
        assert not _is_misclassified_character({"name": "Theron", "type": "character"})


class TestMultiWordDescriptiveNotRejected:
    def test_the_elder_not_rejected(self):
        assert not _is_misclassified_character({"name": "The Elder", "type": "character"})

    def test_a_younger_woman_not_rejected(self):
        assert not _is_misclassified_character({"name": "A younger woman", "type": "character"})


class TestMisclassifiedLocationRejected:
    def test_feast_rejected(self):
        assert _is_misclassified_location({"name": "feast", "type": "location"})

    def test_celebration_rejected(self):
        assert _is_misclassified_location({"name": "celebration", "type": "location"})

    def test_the_feast_location_rejected(self):
        assert _is_misclassified_location({"name": "the feast", "type": "location"})

    def test_a_celebration_rejected(self):
        assert _is_misclassified_location({"name": "a celebration", "type": "location"})


class TestValidLocationNotRejected:
    def test_the_longhouse_not_rejected(self):
        assert not _is_misclassified_location({"name": "the longhouse", "type": "location"})

    def test_dense_forest_not_rejected(self):
        assert not _is_misclassified_location({"name": "dense forest", "type": "location"})
