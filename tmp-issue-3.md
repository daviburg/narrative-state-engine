## Problem

The LLM can return multiple `is_new: true` entities from the same turn that are semantic duplicates. Example: `char-elder` and `char-eldorman` both created at turn-016.

## Root Cause

`_dedup_catalogs()` skips Levenshtein for stems < 6 chars ("elder" = 5). Token overlap misses it ("elder" != "eldorman" as tokens). Character substring ("elder" in "eldorman") isn't checked.

## Proposed Fix

After the discovery LLM returns entities for a turn, check all `is_new: true` pairs for:
1. Character substring match on names (min length >= 4)
2. Levenshtein distance <= 3 on names
3. SequenceMatcher ratio >= 0.6

Merge the lower-confidence one into the higher-confidence one. ~30 lines, mirrors existing `dedup_audit` logic.

Also add character-level substring check to `_dedup_catalogs()` fuzzy pass as a safety net.

## Evidence

From B70 175-turn extraction: `char-elder` and `char-eldorman` both first_seen turn-016, clearly the same person.
