## Problem

43 items after 175 turns, many are ephemeral scene props with no narrative significance: `item-coldwater`, `item-meltedsnow`, `item-usedbowlsutensils`, `item-woodendishes`, `item-bowl`, `item-ladle`.

## Root Cause

Discovery prompt doesn't distinguish narrative-significant items from scene props. No post-extraction pruning exists for low-value entities.

## Proposed Fix

After extraction, identify items with <=1 event reference after N turns since `first_seen_turn`. Remove or mark as "scene-prop" status. Safe (post-hoc, reversible), uses existing orphan-sweep patterns.

## Evidence

From B70 175-turn extraction: 43 items, many are one-shot scene props that add noise without narrative value.
