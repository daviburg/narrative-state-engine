## Per-turn extraction performance degrades linearly with catalog growth

### Problem
Extraction time per turn degrades from ~28s (turns 1-25) to ~210s (turns 276-300) — a 7.5x slowdown. The degradation is linear (~0.5s added per turn processed) with no stabilization. For a 500-turn campaign, estimated per-turn time would be ~286s (~4.8 min/turn).

### Root Cause (3 compounding factors)

**All degradation is in the parallel phase (detail + PC + relationships + events). Discovery is constant.**

1. **Unbounded non-PC entity context**: Entity-detail prompts include the full catalog entry for non-PC entities. PC has trimming (last 3 volatile snapshots, digested history) but non-PC entities do not. Top entities reach 10+ KB.

2. **Unbounded relationship context**: `_collect_existing_relationships()` dumps ALL relationships for ALL mentioned entities into the relationship-mapper prompt. char-player accumulated 121 relationships. This grows quadratically with narrative length.

3. **Growing parallel task count**: More entities are matched per turn as the catalog grows (3.0 proposals/turn at start → 7.8 at turn 200+). Each matched entity triggers a sequential LLM call on a single-slot server.

### Evidence
- Linear regression: slope = 526ms/turn, R^2 = 0.311
- Step function at turn ~126 when catalog reaches ~100 entities
- relationship-index.json reached 183 KB
- char-player at 98 KB (trimmed in prompt but still large)
- Avg entity-detail calls: 2/turn at start → 6-8/turn late

### Proposed Fixes (P0)

1. **Budget relationship context**: Cap relationships per entity in the mapper (top N most recent, or only between currently-mentioned entities)
2. **Trim non-PC entity-detail prompts**: Apply PC-style trimming to all entities — cap volatile_state snapshots, relationship history
3. **Cap parallel entity-detail extractions**: Skip re-extraction for entities recently updated with no new info
4. **Relationship-mapper focus**: Only pass relationships between entities mentioned in the current turn

### Acceptance Criteria
- Per-turn time at turn 300 should be < 90s (vs current 210s)
- No regression in entity quality
- A/B test: extract turns 200-225 with and without the fix, compare times and entity output
