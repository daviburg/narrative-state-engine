# PR #387 Retrospective — Context Budget Optimizations

## Key Facts
- **PR**: #387 `feat: extend context budget architecture to relationship-mapper and entity-detail prompts`
- **Branch**: `feat/context-budget-optimizations`
- **Issues**: Closes #385 (perf: linear extraction degradation), Partially addresses #386 (quality issues)
- **Scope**: +1072/-45 across 6 files (semantic_extraction.py, llm.json, architecture.md, roadmap.md, usage.md, test_context_optimizations.py)
- **Commits**: 4 (feat, docs, fix for review findings, docs fix for review findings)
- **Reviews**: Copilot automated review (10 comments), human @reviewer review (2 blocking + 2 minor)
- **Squad iterations**: 2 (initial commit → review → fix → re-review)

## Three Context Optimization Approaches

All behind `config/llm.json` flags (default: false):

1. **`relationship_relevance_scoring`** — 3-tier priority system for relationship context
   - Tier 1 (full): both endpoints mentioned in current turn
   - Tier 2 (current+last): one endpoint mentioned + updated within 15 turns
   - Tier 3 (summary): one endpoint mentioned + active status
   - Omit: dormant/resolved unless both endpoints mentioned
   - Token budget: 20% of context window with tier degradation

2. **`arc_aware_compression`** — Generalizes PC-only volatile digest to all entities
   - History arrays capped to 3 entries
   - Entries older than 50 turns digested to summary
   - Resolved relationships compressed to one-line notation

3. **`scene_scoped_detail`** — Trims non-PC catalog entries in entity-detail prompt
   - Volatile state: digested + capped to 3 entries per key
   - Relationships: filtered to mentioned + recent (20 turns), capped at 15
   - Stable attributes preserved in full

## A/B Test Results (Turns 200-203 on Arclight)

| Metric | Baseline | Optimized | Delta |
|--------|----------|-----------|-------|
| Avg time/turn | 182.5s | 131.8s | **-28%** |
| Rel. mapper tokens | 34,532 | 7,629 | **-78%** |
| Context overflows | 4/4 turns | 0/4 turns | **Eliminated** |
| Entities found | 2 | 3 | +1 (better) |
| Events found | 6 | 9 | +3 (better) |
| Discovery accuracy | 78% | 83% | +5% |

Key insight: Baseline was already broken — relationship mapper overflowed 32K context every turn, causing silent truncation. Optimized variant improved quality because prompts actually fit.

## Review Findings — Anti-patterns to Watch

1. **Text-matching on IDs is a recurring anti-pattern**: `_trim_entry_for_scene` checked `tid.lower() in text_lower` where tid is like `char-elder-malachar`. Entity IDs never appear in DM prose. Fix: use pre-computed `mentioned_ids` set from caller with proper name/alias matching.
2. **Dead code in conditional branches**: tier-4 elif had `both_mentioned` which was unreachable (both_mentioned entities always score higher).
3. **Missing budget overflow check**: Final degradation in `_format_relationships_budgeted` had no budget check for tier-1-only case.
4. **Doc mismatches**: usage.md field name didn't match code; architecture.md relationship claim was inaccurate.

## Follow-up Items (Deferred from Review)

1. Copilot review: prior-state block can still inject full relationship list with arc_aware + no scene_scoped (low confidence, suppressed)
2. Longer A/B test needed (25+ turns) to validate quality at scale
3. #386 quality issues still open (phantom chars, stale relationships, timeline incoherence)
4. Enable flags by default once validated at scale
5. Consider per-prompt token budget telemetry dashboard
6. Tier degradation could use exponential backoff instead of linear
