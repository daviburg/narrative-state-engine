## Problem

Same physical location described differently as it evolves across turns:
- `loc-shelters` (turn-122) -> `loc-communal-home` (turn-124) -> `loc-longhouse` (turn-143)
- `loc-arctic` (turn-113) -> `loc-arcticwild` (turn-141)

No dedup mechanism understands narrative evolution — names share zero tokens.

## Root Cause

All existing dedup (name similarity, Levenshtein, token overlap) operates on string similarity. Narrative evolution produces completely different names for the same entity.

## Proposed Fix

Run `dedup_audit.py` periodically during extraction (every 25-50 turns) with an enhanced LLM prompt that includes: "Same location that was renamed/rebuilt/upgraded counts as same entity."

This uses existing infrastructure with low risk.

## Evidence

From B70 175-turn extraction: shelters/communal-home/longhouse are all the same structure at different stages. arctic/arcticwild are the same setting.
