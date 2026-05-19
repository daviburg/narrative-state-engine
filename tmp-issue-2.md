## Problem

`_merge_pc_aliases()` failed to catch `char-fenouille-moonwind` as a PC alias fork, even though `char-player` already had "Fenouille" as an alias. The entity persisted from turn-059 through turn-091 as a separate character.

## Root Cause

Two guards block the merge:
- Turn-span guard (<=3 turns): Entity spans 32 turns
- Event-text threshold (>=2 mentions): Name appears in zero PC event descriptions

The merger never checks existing PC aliases against candidate entity names.

## Proposed Fix

After existing guards, add a check: if any token of the candidate's name matches an existing PC alias (>=4 chars, capitalized), flag it as a PC alias variant. "Fenouille" is already a known PC alias -> "Fenouille Moonwind" contains it -> merge.

This bypasses both the event-text and turn-span guards using already-validated alias data.

## Evidence

From B70 175-turn extraction: `char-fenouille-moonwind` ("Fenouille Moonwind") is the player character. `char-player` already has "Fenouille" as alias.
