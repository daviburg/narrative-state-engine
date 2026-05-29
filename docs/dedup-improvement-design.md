# Dedup Improvement Design — A/B Test Plan

## Problem Statement

- The 30-turn Qwen3.6-35B-A3B evaluation scored **6/10 on deduplication** (lowest dimension)
- Duplicates arise from: coreference failures (same entity with different surface forms), cross-catalog blind spots (entity vs location overlap), and late dedup (duplicates already have detail extraction invested)
- Related issues: #337 (14 duplicate locations), #421 (same-turn dedup causes ~30% character regression), #420 (body-part filter causes faction/event regressions)

## Proposed Changes (3 interventions)

### 1. Cross-Catalog Pre-Detail Dedup Gate

- **Where**: Between phase 1 (entity-discovery) and phase 2 (entity-detail) in `tools/semantic_extraction.py`
- **What**: After discovery produces candidate entities for a turn, run a dedup check against ALL existing catalog entries (entities AND locations) BEFORE requesting detail extraction
- **Why**: While within-turn dedup (`_within_turn_dedup`) already runs before detail extraction for same-turn `is_new` string-similarity cases, cross-catalog dedup (against existing catalog entries from prior turns) and periodic/post-batch dedup both run AFTER detail extraction — wasting LLM calls on cross-turn duplicates and making merge harder
- **Mechanism**: Fuzzy name match (token overlap) + type compatibility check + optional LLM confirmation for borderline cases
- **Expected impact**: Prevent 30-50% of duplicates from ever getting detailed, saving tokens and improving quality

### 2. Strengthen Coreference Examples in Discovery Template

- **Where**: `templates/extraction/entity-discovery.md`
- **What**: Add 3-5 explicit coreference examples showing common RPG patterns:
  - Title changes: "the guard captain" → "Captain Harland" (same person)
  - Pronoun groups: "the kobolds" → "three kobold scouts" (subset vs group)
  - Location aliases: "the tavern" → "The Rusty Nail" → "the inn" (same place)
  - Descriptor shifts: "the hooded figure" → "Zara" (identity revealed)
- **Why**: The model produces duplicates when surface forms change between turns; explicit examples in the template teach it to use `existing_id` instead of creating new entries
- **Expected impact**: 20-40% fewer duplicate discoveries at source
- **Status**: ✅ Implemented in PR #443. Six coreference patterns are now documented in the template's rules section:
  - Title/rank changes (e.g. "the guard" → "Captain Harland")
  - Identity reveals (e.g. "the hooded figure" → "Zara")
  - Location aliases (e.g. "the tavern" → "The Rusty Nail" → "the inn")
  - Group vs. subset (e.g. "the kobolds" faction → "three kobold scouts")
  - Shortened names (e.g. "the elder shaman" → "the elder" → "the shaman")
  - Definite descriptions referring to a previously catalogued place

### 3. Reduce Periodic Dedup Interval (50 → 25 turns)

- **Where**: `tools/semantic_extraction.py`, dedup scheduling logic
- **What**: Run the full dedup audit every 25 turns instead of every 50
- **Why**: Duplicates compound — a duplicate at turn 10 generates duplicate relationships at turns 11-49 before being caught at turn 50. Catching earlier reduces cascade damage.
- **Expected impact**: Fewer cascaded duplicates, cleaner relationship graph

## A/B Test Design

### Baseline (Variant A — current pipeline, no changes)

- Current templates + current dedup logic, unmodified
- Run on turns 1-30 (per `docs/ab-test-standard.md` mini-set)
- 3 runs at temp=0.3

### Treatment (Variant B — all 3 interventions applied)

- All 3 interventions applied simultaneously: cross-catalog dedup gate, strengthened coreference examples, reduced dedup interval (50 → 25)
- Same turns 1-30, 3 runs at temp=0.3

### Metrics

- **Entity count**: Total unique entity count should remain stable (within PASS threshold: Δ ≤ 5% loss from baseline) — the goal is fewer *duplicates*, not fewer entities overall
- **Dedup audit score**: Run `python tools/dedup_audit.py --catalog-dir <variant-dir>/catalogs` for each A/B variant output directory (e.g. `framework-ab-a-run1/catalogs` and `framework-ab-b-run1/catalogs`), count suspected duplicates (`auto_merged + flagged_for_review` from summary) — goal: ≤50% of baseline's suspected duplicates
- **LLM calls per turn**: expect 15-25% fewer (detail calls saved)
- **Manual spot-check**: 10 random entities, count false merges (must be 0)
- **Quality regression**: per-type entity coverage must not drop >5% vs baseline for any individual type (characters, locations, items, factions, events), measured via per-type catalog file counts per `docs/ab-test-standard.md` §3.1 — distinct from **Entity count** which tracks the aggregate total; `tests/fixtures/extraction-ground-truth-turns-1-30.json` is for manual spot-check review only and is not machine-counted

### Success Criteria

- Dedup audit finds ≤50% of baseline suspected duplicates
- Zero false merges in spot-check
- Entity coverage within 5% of baseline
- Wall-clock time within 120% of baseline (dedup gate adds overhead)

## Implementation Sequence

1. Implement cross-catalog dedup gate in `tools/semantic_extraction.py` (behind feature flag)
2. Update `templates/extraction/entity-discovery.md` template with coreference examples
3. ~~Change the default `dedup_audit_interval` from 50 to 25~~ — closed without merging
4. Run A/B test per `docs/ab-test-standard.md`
5. Evaluate combined results with template fix as new baseline

## A/B Test Results (100 turns, temp=0, Qwen3.6-35B-A3B-UD-Q4_K_M)

### Baseline (Variant A — eval-qwen36-100t-temp0-clean)

| Metric | Value |
|--------|-------|
| Characters | 14 |
| Locations | 6 |
| Items | 6 |
| Factions | 2 |
| Events | 45 |
| Dedup candidates (post-hoc) | 2 (both correctly discarded) |
| In-flight merges | 1 (item-carving-tool + item-tool) |
| Failures | 0/100 |
| Runtime | 1h 31m |

### Intervention #2 — Coreference Template Examples ✅ MERGED (PR #443)

| Metric | Baseline | Intervention #2 | Delta |
|--------|----------|-----------------|-------|
| Characters | 14 | 13 | -1 (removed vague duplicates) |
| Locations | 6 | 5 | -1 (consolidated aliases) |
| Items | 6 | 8 | +2 (more specific names) |
| Factions | 2 | 2 | 0 |
| Events | 45 | 51 | +6 (+13%) |
| Dedup candidates | 2 | 1 | -1 (cleaner) |
| Runtime | 1h31m | 1h08m | -23min |

**Key wins:** Fixed the elder/older-figure dedup failure, removed vague entities
(special-someone, unknown-captors), improved item/location naming specificity.

### Combined Eval Results (template fix on main as baseline)

After merging the coreference template fix (PR #443), interventions #1 and #3 were
re-evaluated against the new baseline to determine incremental value.

| Metric | Main (template fix) | + Gate (#1) | + Interval-25 (#3) |
|--------|---------------------|-------------|---------------------|
| Characters | 13 | 10 (-3) | 12 (-1) |
| Locations | 5 | 7 (+2) | 6 (+1) |
| Items | 8-9 | 10 (+2) | 8 (-1) |
| Factions | 2-3 | 3 (0) | 3 (+1) |
| Events | 47-48 | 50 (+3) | 51 (+3) |
| Duration | ~1h47m | ~1h52m | ~1h45m |

**Findings:**

- **Cross-catalog gate (#1)**: Removes 3 characters (13→10) vs the template-fixed
  baseline — likely over-deduplicating now that the coreference template fix handles
  the root cause. Net +1 total entities but character loss is a regression signal.
- **Interval reduction (#3)**: Negligible impact — -1 character, net zero total
  entities, +4min runtime overhead. No meaningful benefit.

## Decision

Both interventions #1 and #3 **closed without merging** (branches deleted).

The coreference template fix (PR #443, merged) was the dominant improvement. The
cross-catalog gate risks over-deduplication when combined with the template fix. The
interval reduction provides no meaningful benefit.

## Final Status

**Dedup improvement work COMPLETE.** Only the template fix (intervention #2, PR #443)
was merged. The remaining interventions demonstrated no incremental value on top of
the template fix and were abandoned.

## Related Issues

- #337 — discovery creates 14 duplicate locations
- #421 — same-turn dedup causes ~30% character regression
- #420 — body-part filter causes faction/event regressions
- #386 — wiki data quality issues phase 1
- #413 — migrate hardcoded word lists to data templates
- Zero false merges in spot-check
- Entity coverage within 5% of baseline
- Wall-clock time within 120% of baseline (dedup gate adds overhead)

## Implementation Sequence

1. Implement cross-catalog dedup gate in `tools/semantic_extraction.py` (behind feature flag)
2. Update `templates/extraction/entity-discovery.md` template with coreference examples
3. Change the default `dedup_audit_interval` from 50 to 25 in `config/llm.json`
4. Run A/B test per `docs/ab-test-standard.md`
5. If successful, remove feature flag and merge

## Blocking Dependencies

- **Blocked by**: 100-turn eval completing (establishes quality baseline at scale)
- **Blocks**: Template A/B execution (issues #337, #421, #420 fixes)

## Related Issues

- #337 — discovery creates 14 duplicate locations
- #421 — same-turn dedup causes ~30% character regression
- #420 — body-part filter causes faction/event regressions
- #386 — wiki data quality issues phase 1
- #413 — migrate hardcoded word lists to data templates
