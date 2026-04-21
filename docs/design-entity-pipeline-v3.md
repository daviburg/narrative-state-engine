# Entity Pipeline V3 — Design Document

Investigation into issues #106, #107, #108: entity discovery gaps, detail extraction stalls, and ID inconsistencies.

---

## 1. Current Pipeline Flow

```
For each turn:
  ┌──────────────────────────────────────────────────────────┐
  │ 1. ENTITY DISCOVERY                                      │
  │    Input:  turn text + known-entities table               │
  │    LLM:    entity-discovery.md template                   │
  │    Output: list of {name, type, is_new, existing_id,      │
  │            proposed_id, confidence}                        │
  │    Filter: confidence >= 0.6                               │
  ├──────────────────────────────────────────────────────────┤
  │ 2. ENTITY DETAIL EXTRACTION (per discovered entity)       │
  │    Input:  turn text + entity ref + current catalog entry  │
  │    LLM:    entity-detail.md template                      │
  │    Output: full entity object (V2 schema)                  │
  │    Post:   _coerce_entity_fields() → validate → merge     │
  ├──────────────────────────────────────────────────────────┤
  │ 2b. PC DETAIL EXTRACTION (always, if not in step 2)       │
  │    Same as step 2, but for char-player specifically        │
  │    Triggered: every turn where PC wasn't in discovery      │
  ├──────────────────────────────────────────────────────────┤
  │ 3. RELATIONSHIP MAPPING                                   │
  │    Input:  turn text + mentioned entities (from discovery) │
  │    LLM:    relationship-mapper.md template                 │
  │    Filter: requires >= 2 mentioned entities                │
  ├──────────────────────────────────────────────────────────┤
  │ 4. EVENT EXTRACTION                                       │
  │    Input:  turn text + entity_ids from discovery + PC      │
  │    LLM:    event-extractor.md template                    │
  │    Output: list of events with related_entities            │
  │    CRITICAL: entity_ids passed = discovery results only    │
  └──────────────────────────────────────────────────────────┘
  
  Post-batch: _dedup_catalogs() merges duplicate entities
              _rewrite_stale_ids() fixes dangling references
```

### Key data flow dependencies

- **Discovery → Detail**: Only entities above confidence threshold get detail extraction.
- **Discovery → Events**: The `entity_ids` list passed to the event extractor comes from the discovery `qualified` list + `char-player`. The LLM is told "Use the entity IDs provided in the context" but **frequently invents new IDs** for entities it sees in the text.
- **Discovery → Relationships**: Only entities in the `qualified` list are included. If discovery misses an entity, it gets no relationships.
- **Known entities list**: Grows with each new entity. Passed as the full roster table (id | name | type — description) to every discovery call.

### ID generation sources

| Source | Where IDs are created | Normalization applied |
|---|---|---|
| Entity discovery (LLM) | `proposed_id` field for new entities | `fix_id_prefix()` corrects wrong type prefix |
| Entity detail (LLM) | `id` field in returned entity | `_coerce_entity_fields()` handles comma-splits |
| Event extraction (LLM) | `related_entities` array | **None** — IDs pass through unvalidated |
| Catalog merger | Matches by exact string `==` comparison | **Case-sensitive**, no normalization |
| Dedup pass | Post-batch only, name/alias/token overlap | Merges duplicates, rewrites references |

---

## 2. Root Cause Analysis

### 2.1 Why Entity Discovery Stops Finding New Entities (#106)

**Observation**: Entity count progression from Run 4 extraction log:

| Turn range | Entity count | New entities |
|---|---|---|
| turn-001 to turn-025 | 27 | 27 |
| turn-026 to turn-050 | 54 | 27 |
| turn-051 to turn-075 | 72 | 18 |
| turn-076 to turn-100 | 78 | 6 |
| turn-101 to turn-125 | 82 | 4 |
| turn-126 to turn-150 | 83 | 1 |
| turn-151 to turn-345 | 83 | **0** |

Discovery stops producing new entities after ~turn-150 despite major named characters (Kael, Tala, Lena, Borin, Gorok, Lyra, Thorne, Maelis, etc.) appearing throughout turns 149–343.

**Root causes** (compounding):

1. **Growing known-entities context overwhelms the LLM**. By turn-100, the known-entities table is ~83 entries (~2,900 tokens). The discovery template's system prompt is already ~3,000 tokens. Combined with turn text (~500–2,000 tokens), the total prompt is 6,000–8,000 tokens. The 14B local model (qwen2.5:14b) has limited effective context and starts timing out or returning empty results.

2. **Discovery template focuses on coreference, not new-entity creation**. The template has extensive coreference instructions ("if a mention refers to an already-known entity... set is_new to false"). As the known-entities list grows, the LLM increasingly matches new characters to existing entries rather than creating new ones. A character named "Kael" might get matched to an existing "the young hunter" by the LLM's inference.

3. **Discovery timeouts produce empty results, not errors**. When discovery fails with `LLMExtractionError`, the code sets `discovery_result = {"entities": []}` and continues. This means zero entities go to detail extraction, relationships, and events. But event extraction still runs with only `char-player` as the entity list — and the event LLM invents IDs for characters it sees in the text. This is the primary source of orphan IDs.

4. **Discovery failures by turn (Run 4 log)**:
   - turn-081, turn-141, turn-145, turn-197, turn-242, turn-294, turn-315, turn-332, turn-335
   - All are timeouts or JSON parse failures — total of 9 discovery failures across 345 turns
   - However, between these failures, discovery IS running — it's just returning only existing-entity matches, not new entities.

5. **Player turns ARE processed** (both DM and player turns go through the loop), but turn-149 (which contains both DM narration and player response text with many named characters) still didn't produce new entity discoveries.

### 2.2 Why PC Detail Extraction Stalls at turn-054 (#107)

**Observation**: `char-player` has `last_updated_turn: turn-054` despite the PC special-case code (step 2b) running every turn.

**Root cause**: **Timeouts and LLM failures silently skip the PC update.**

PC detail extraction failures from the log:
- turn-058: timeout
- turn-064: timeout
- turn-071: timeout
- turn-072: timeout
- turn-086: timeout
- turn-092: timeout
- turn-125: timeout
- turn-175: timeout
- turn-264: timeout
- turn-266: timeout

**10 explicit failures** logged, all timeouts (60-second limit). But the problem is worse: between these logged failures, the PC extraction IS running but the **LLM returns data that fails schema validation**. The log shows many `Entity failed schema validation` warnings (missing `name`, `identity`, `first_seen_turn`) that are not attributed to a specific entity — these are likely PC extraction results that silently fail the `_validate_entity()` check and get dropped.

**Why the growing context causes PC timeouts**: The PC detail prompt includes the full current catalog entry for `char-player`. As the PC accumulates `stable_attributes`, `volatile_state`, and `relationships`, this context grows. The 14B local model takes longer to process each PC extraction call, eventually exceeding the 60-second timeout consistently.

**Compounding factor**: When PC extraction succeeds on some turns but is skipped on others, the `last_updated_turn` only advances when a successful merge happens. This means the PC's `current_status` becomes increasingly stale, causing the LLM to echo old status text rather than generating fresh updates.

### 2.3 Where Entity IDs Diverge (#108)

**Three sources of ID inconsistency**:

#### Source 1: Event extractor invents IDs ad-hoc

The event extractor receives a list of known entity IDs (from discovery), but when the turn text mentions characters by name that aren't in that list, the LLM fabricates IDs. It receives `(none)` or `char-player` as the entity list but sees "Kael", "Tala", "Gorok" in the text and generates `char-kael`, `char-tala`, `char-gorok`.

**Evidence**: evt-270 (turn-329) contains `char-Kael` and `char-Tala` with capital letters, while evt-271 (turn-330) has `char-kael` and `char-tala` lowercase. The LLM generates these independently each turn with no consistency enforcement.

#### Source 2: No ID normalization on event extraction output

The `_coerce_entity_fields()` function normalizes entity data, but **event `related_entities` arrays pass through with zero normalization**. No lowercasing, no prefix validation, no dedup against known catalog IDs.

#### Source 3: Name-based ID generation varies by context

The same character gets different IDs depending on how the LLM interprets context:
- "Anya" → `char-anya`, `char-ananya`, `char-anxa` (typo), `char-anymage` (corruption), `npc-ananya` (wrong prefix)
- "Gorok" → `char-gorok`, `char-warrior-chief-gorok`, `faction-warrior-chief-gorok` (wrong type)
- "Lyra" → `char-lyra`, `char-lyrawyn`, `char-elder-lyra`, `faction-elder-lyra` (wrong type)
- "Thorne" → `char-thorne`, `char-chief-thorne`, `faction-chief-thorne` (wrong type)

The `faction-` prefix variants happen because the LLM treats "Chief Thorne" or "Elder Lyra" as faction leaders and assigns a faction prefix — the event template doesn't validate against known types.

---

## 3. Data Audit

### 3.1 Entity coverage

| Metric | Count |
|---|---|
| Catalog entities (post-dedup) | 51 |
| Unique entity IDs in events | 64 |
| Orphan IDs (in events, no catalog entry) | **34** |
| Orphan IDs as % of event IDs | **53%** |
| Distinct real entities (estimated) | ~45 characters, ~12 locations, ~5 factions, ~18 items ≈ **80** |
| Catalog coverage of real entities | ~64% |

### 3.2 ID variant groups

| Real entity | Variant IDs | Total events | Root cause |
|---|---|---|---|
| Anya/Ananya | `char-anya`, `char-ananya`, `char-anxa`, `char-anymage`, `npc-ananya` | 10 | Name variation + typo + wrong prefix |
| Gorok | `char-gorok`, `char-warrior-chief-gorok`, `faction-warrior-chief-gorok` | 9 | Title inclusion + wrong type |
| Lyra/Lyrawyn | `char-lyra`, `char-lyrawyn`, `char-elder-lyra`, `faction-elder-lyra` | 9 | Name/title variation + wrong type |
| Thorne | `char-thorne`, `char-chief-thorne`, `faction-chief-thorne` | 7 | Title inclusion + wrong type |
| Maelis | `char-maelis`, `char-maelis-of-the-swift-arrows` | 5 | Full title as ID |
| Elder | `char-elder` (catalog), `char-the-elder` | varies | Article inclusion |
| Healer | `char-healer`, `entity-healer` | 7 | Wrong prefix |
| Kael | `char-kael`, `char-Kael` | 16 | Case variation |
| Tala | `char-tala`, `char-Tala` | 7 | Case variation |

After consolidation: **34 orphan IDs map to approximately 15 distinct real entities**, meaning ~19 IDs are pure duplicates/variants.

### 3.3 Major missing characters (5+ events, no catalog entry)

| Character | Events | Turn range | Notes |
|---|---|---|---|
| Kael | 16 | turn-149..turn-343 | Most-referenced orphan. Hunter/scout role. |
| Tala | 7 | turn-149..turn-343 | Named at turn-149 alongside Kael. |
| Healer | 7 | turn-246..turn-343 | May be the same as char-shaman or a separate NPC. |
| Gorok | 9 | turn-314..turn-343 | Warrior chief, late-game character. |
| Lena | 6 | turn-161..turn-301 | Education/children's leader role. |
| Maelis | 5 | turn-333..turn-343 | Late-game "swift arrows" character. |

### 3.4 Discovery failures vs. entity gap timeline

Entity discovery effectively stops at turn-126 (82→83 entities, last new entity at turn-112). From turn-126 to turn-345 (219 turns), zero new entities are discovered despite 15+ distinct new characters appearing in the narrative. The event extractor compensates partially by inventing IDs, but these never flow back to the catalog.

---

## 4. Approach Evaluation

### 4.1 Fixing entity discovery gap (#106)

| Approach | Complexity | LLM cost | Data quality | Correctness |
|---|---|---|---|---|
| **A: Event back-propagation** (post-processing scan of orphan event IDs → create stubs → run detail extraction) | Low | Medium (re-reads relevant turns) | Medium (stubs are thin until detail runs) | Good (catches all orphans) |
| **B: Discovery loop integration** (after events, feed new IDs back into discovery → immediate detail) | Medium | Low (no re-reading) | High (entities created at right time) | Good (but only catches entities events mention) |
| **C: Periodic full re-scan** (every N turns, re-run discovery across recent events) | Medium | High (batch re-discovery) | High | Good (catches everything but delayed) |

**Recommended: B (discovery loop integration) + A (post-batch cleanup)**

Approach B handles the common case (event extractor finds named characters the discovery step missed) cheaply by feeding orphan event IDs back into the discovery→detail pipeline within the same turn. Approach A runs as a post-batch pass to catch anything still missing.

### 4.2 Fixing PC detail extraction stalls (#107)

| Approach | Complexity | Impact |
|---|---|---|
| **Increase timeout** for PC extraction (120s vs 60s) | Trivial | Partial fix — larger models still slow |
| **Trim PC context** before sending to LLM (only recent volatile_state, summarized stable_attributes) | Low | Good — reduces prompt size, fewer timeouts |
| **Periodic (not every-turn) PC extraction** | Low | Good — reduces total LLM calls, batches updates |
| **Schema validation fallback** — when validation fails, attempt partial merge of valid fields | Medium | Good — recovers data from near-valid responses |

**Recommended: Trim PC context + increase timeout + validation fallback**

The PC extraction runs 345 times (every turn) and fails frequently due to growing context. Trimming the prior entity state to just identity + recent volatile_state + key stable_attributes would keep the prompt under 4K tokens. Combined with a 120s timeout and graceful handling of near-valid responses, this should fix the stall without changing the extraction cadence.

### 4.3 Fixing ID inconsistencies (#108)

| Approach | Complexity | Coverage | Reliability |
|---|---|---|---|
| **A: Normalize at extraction time** (lowercase all IDs, enforce prefix, canonical name lookup) | Low | Partial (LLM still invents) | Medium |
| **B: Post-extraction normalization pass** (fuzzy-match IDs, merge) | Medium | Complete | High |
| **C: Template-level guidance** (provide known entity list to event extractor, stronger ID rules) | Low | Partial (LLM compliance varies) | Low |
| **D: Combined** (A + B) — normalize at extraction time, fuzzy dedup post-pass | Medium | Complete | High |

**Recommended: D (combined) with emphasis on event ID normalization**

The highest-impact single change is adding ID normalization to event extraction output — lowercasing, prefix validation, and lookup against catalog IDs. This catches ~80% of the variants (case, prefix). The remaining ~20% (name variants like anya/ananya, lyra/lyrawyn) need the fuzzy dedup post-pass which already exists in `_dedup_catalogs()` but needs to be extended to event `related_entities`.

---

## 5. Recommended Design

### Phase 1: Event ID normalization (addresses #108, partially #106)

**Scope**: Small, low-risk changes to `semantic_extraction.py` and `catalog_merger.py`.

1. **Normalize event `related_entities` after extraction**: lowercase, validate prefix, lookup against catalog.
2. **Add `normalize_entity_id(raw_id, catalogs)` function** that:
   - Lowercases the ID
   - Validates/fixes prefix using existing `fix_id_prefix()` logic
   - Fuzzy-matches against known catalog IDs (Levenshtein distance ≤ 2 or token overlap)
   - Returns canonical ID if match found, normalized ID otherwise
3. **Apply normalization** to event `related_entities` before `merge_events()`.

### Phase 2: Event-driven entity creation (addresses #106)

**Scope**: Medium change to `extract_and_merge()` in `semantic_extraction.py`.

1. After event extraction (step 4), collect `related_entities` IDs not in catalogs.
2. For each orphan ID:
   - Create a stub entity with: id, inferred name (from ID), inferred type (from prefix), first_seen_turn.
   - Optionally run detail extraction for the stub using the current turn text.
3. This converts the event extractor's ad-hoc ID invention into a feature rather than a bug.

### Phase 3: PC extraction resilience (addresses #107)

**Scope**: Small changes to PC extraction path.

1. **Trim PC context**: Limit prior entity context to identity + last 3 volatile_state snapshots + key stable_attributes only.
2. **Increase PC timeout**: Use 120s for PC extraction specifically (or make configurable per-agent).
3. **Graceful validation fallback**: When `_validate_entity()` fails for char-player, attempt to extract and merge only the valid fields (`current_status`, `volatile_state`) rather than discarding the entire response.

### Phase 4: Post-batch reconciliation (catch-all)

**Scope**: Enhancement to existing `_dedup_catalogs()` post-batch pass.

1. **Extend dedup to events**: After entity dedup, also rewrite event `related_entities` using the merge map (already done in `_rewrite_stale_ids()`).
2. **Orphan sweep**: After dedup, scan events for remaining orphan IDs. Create stub entities for any with 3+ event references.
3. **Back-propagation detail pass**: For stub entities created in step 2, re-read the first relevant transcript turn and run detail extraction to fill in identity/status.

---

## 6. Implementation Plan

| Order | Change | Dependencies | Estimated scope |
|---|---|---|---|
| 1 | Add `normalize_entity_id()` to `catalog_merger.py` | None | ~50 lines |
| 2 | Apply normalization to event `related_entities` in `extract_and_merge()` | Step 1 | ~10 lines |
| 3 | Add orphan-ID feedback loop in `extract_and_merge()` (stub creation from event IDs) | Step 2 | ~40 lines |
| 4 | Trim PC prior context in `_format_prior_entity_context()` | None | ~20 lines |
| 5 | Add configurable per-agent timeout (or just increase PC timeout) | None | ~10 lines |
| 6 | Add validation fallback for PC extraction | None | ~30 lines |
| 7 | Extend post-batch orphan sweep + back-propagation | Steps 1–3 | ~60 lines |
| 8 | Add integration tests for ID normalization and orphan feedback | Steps 1–3 | ~100 lines |
| 9 | Re-run extraction on session-import to validate | Steps 1–8 | Runtime only |

### Suggested issue/branch structure

- **#108 fix** (Phase 1): `fix/issue-108-id-normalization` — steps 1–2
- **#106 fix** (Phase 2): `feat/issue-106-event-entity-feedback` — step 3
- **#107 fix** (Phase 3): `fix/issue-107-pc-extraction-resilience` — steps 4–6
- **Phase 4** (reconciliation): `feat/post-batch-entity-reconciliation` — step 7
- Tests: Include in each branch as relevant

### Validation criteria

After implementing all phases, re-run extraction on session-import and verify:
- [ ] Entity count ≥ 65 (up from 51 post-dedup)
- [ ] Orphan event IDs ≤ 5 (down from 34)
- [ ] All ID variant groups consolidated to single canonical IDs
- [ ] `char-player` `last_updated_turn` advances past turn-100
- [ ] No new false-positive dedup merges (regression check)
- [ ] Kael, Tala, Lena, Borin, Gorok all have catalog entries with detail
