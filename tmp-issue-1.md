## Problem

`_is_misclassified_location()` in `semantic_extraction.py` has a 5-word exact-match set that doesn't catch body parts or personal attributes when prefixed with possessive pronouns. Example: `loc-lips` ("his lips") persisted through 77 turns as a location entity.

## Root Cause

The filter doesn't strip possessive pronouns (`his/her/their/my/your/its`), and has no head-noun check — unlike `_is_misclassified_character()` which is much more robust.

## Proposed Fix

Add a possessive-pronoun prefix check to `_is_misclassified_location()`:
- If a location name starts with `his/her/their/my/your/its`, reject it
- Generic (no body-part blocklist needed), catches "his lips", "his shoulders", "her hands"
- Near-zero false-positive risk — real places are never named "his forest"

## Evidence

From B70 175-turn extraction: `loc-lips` ("his lips") first_seen turn-098, persisted to turn-175.
