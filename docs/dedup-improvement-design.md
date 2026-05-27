# Dedup Improvement Design — A/B Test Plan

## Problem Statement

- The 30-turn Qwen3.6-35B-A3B evaluation scored **6/10 on deduplication** (lowest dimension)
- Duplicates arise from: coreference failures (same entity with different surface forms), cross-catalog blind spots (entity vs location overlap), and late dedup (duplicates already have detail extraction invested)
- Related issues: #337 (14 duplicate locations), #421 (same-turn dedup causes ~30% character regression), #420 (body-part filter causes faction/event regressions)

## Proposed Changes (3 interventions)

### 1. Cross-Catalog Pre-Detail Dedup Gate

- **Where**: Between phase 1 (entity-discovery) and phase 2 (entity-detail) in `tools/semantic_extraction.py`
- **What**: After discovery produces candidate entities for a turn, run a dedup check against ALL existing catalog entries (entities AND locations) BEFORE requesting detail extraction
- **Why**: Currently dedup runs AFTER detail extraction — wasting LLM calls on duplicates and making merge harder
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

### 3. Reduce Periodic Dedup Interval (50 → 25 turns)

- **Where**: `tools/semantic_extraction.py`, dedup scheduling logic
- **What**: Run the full dedup audit every 25 turns instead of every 50
- **Why**: Duplicates compound — a duplicate at turn 10 generates duplicate relationships at turns 11-49 before being caught at turn 50. Catching earlier reduces cascade damage.
- **Expected impact**: Fewer cascaded duplicates, cleaner relationship graph

## A/B Test Design

### Baseline (Variant A — current pipeline, no changes)

- Current templates + current dedup logic, unmodified
- Run on turns 1-30 (per ab-test-standard.md mini-set)
- 3 runs at temp=0.3

### Treatment (Variant B — all 3 interventions applied)

- All 3 interventions applied simultaneously: cross-catalog dedup gate, strengthened coreference examples, reduced dedup interval (50 → 25)
- Same turns 1-30, 3 runs at temp=0.3

### Metrics

- **Entity count**: expect 5-15% fewer entities (WARN threshold: >5%, BLOCK threshold: >15%)
- **Dedup audit score**: `python tools/dedup_audit.py --catalog-dir sessions/<session>/framework/catalogs/`, count suspected duplicates (lower = better)
- **LLM calls per turn**: expect 15-25% fewer (detail calls saved)
- **Manual spot-check**: 10 random entities, count false merges (should be 0-1)
- **Quality regression**: entity coverage must not drop >5% vs baseline (entity coverage = count of distinct narrative entities identified ÷ count from manual ground-truth annotation of same turns)

### Success Criteria

- Dedup audit finds ≤50% of baseline suspected duplicates
- Zero false merges in spot-check
- Entity coverage within 5% of baseline
- Wall-clock time within 120% of baseline (dedup gate adds overhead)

## Implementation Sequence

1. Implement cross-catalog dedup gate in `tools/semantic_extraction.py` (behind feature flag)
2. Update `entity-discovery.md` template with coreference examples
3. Change the default `dedup_audit_interval` from 50 to 25 in `config/llm.json`
4. Run A/B test per `ab-test-standard.md`
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
