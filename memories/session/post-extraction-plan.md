# Post-Extraction Plan (Session 2025-05-15)

## Situation
- Full 344-turn extraction complete on `fix/entity-detail-scaling` branch
- PR #390 merged on main (review comment fixes for #388/#389)
- Scaling fix (1 commit: `b14d829`) is NOT on main — needs its own PR
- B7 (turns 301-344) averaged 322s/turn despite 6-call cap — root cause TBD
- 10 phantom characters detected (abstract concepts misclassified)
- 525 entity updates capped — quality impact unknown
- Squad composition gaps identified

## Task Sequence (dependency-ordered)

### Phase 1: Squad Composition & Tooling (prerequisite for all else)
1. **Design @token-economist agent** — owns context budget strategy, prompt compression, quality-vs-cost tradeoffs
2. **Design @quality-analyst agent** — owns output correctness evaluation, ground truth comparison, hallucination detection, semantic accuracy scoring
3. **Reflect on further gaps** — prompt engineer? extraction-validator?
4. **Create agent definitions** — PR with new .agent.md files + coordinator update

### Phase 2: Land the Scaling Fix
5. **Rebase `fix/entity-detail-scaling` on main** (main now has PR #390 changes, scaling branch diverged from older main)
6. **Resolve conflicts** (both branches modified same area of semantic_extraction.py)
7. **Run squad loop** — tests, CI, review, merge

### Phase 3: B7 Slowdown Investigation (token-economist + developer)
8. **Root cause analysis** — why 322s/turn despite 6-call cap? Where do the seconds go?
   - Hypothesis A: per-call tokens still growing (6 calls × 8K+ tokens each = 48K+ total)
   - Hypothesis B: relationship scoring pre-pruning is expensive (43K→6K pruning seen)
   - Hypothesis C: discovery retries (truncation) at large catalog size
9. **Design budget strategy** — fixed total token budget per turn, allocated across phases
10. **Implement and validate** — code changes, A/B test on turns 301-320

### Phase 4: Phantom Mitigation (model-optimizer + token-economist)
11. **Analyze phantom patterns** — when do they appear? what discovery prompt produced them?
12. **Prompt engineering approach** — modify entity-discovery template to reject abstractions
13. **Self-review pass design** — add a lightweight "validation" LLM call post-discovery that asks "is this entity a real in-world thing or an abstract concept?"
14. **A/B test** — compare hallucination rate with/without prompt fix and/or review pass
15. **Cost analysis** — is the extra validation call worth the quality gain?

### Phase 5: Output Quality Analysis (quality-analyst + extraction-specialist)
16. **Entity accuracy** — compare extracted entities to ground truth / manual review
17. **Relationship completeness** — only 293 edges for 344 turns — is this correct or undertracted?
18. **Event coverage** — 639 events — spot-check for missed/hallucinated events
19. **Impact of capping** — compare entities that were capped (never got detail calls) to entities that weren't — do capped entities have stale/missing data?
20. **Turn coverage** — are any entity types (items, factions) systematically undertracted?

## Decision Points (require human input)
- Should the scaling fix land as-is (known B7 slowdown) or wait for B7 fix?
- Token budget: fixed cap per turn vs. adaptive (proportional to turn complexity)?
- Phantom fix: prompt-only vs. prompt + validation pass (cost tradeoff)?
- Quality bar: what entity/relationship counts are "good enough"?

## Branch State
- `main`: PR #390 merged (commit 4f46bf2)
- `fix/entity-detail-scaling`: 1 commit ahead (b14d829), needs rebase
- Extraction output: `framework-local/ab-test/v2-full-optimized/` (344 turns)
