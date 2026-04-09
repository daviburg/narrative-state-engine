# Lessons Learned & Extraction Specification

## Narrative-State Engine (Quiet Weave Case Study)

---

# 1. Lessons Learned

## 1.1 Summary is not State

A narrative summary is inherently lossy.
A simulation-grade system requires **state preservation, not summarization**.

Failure observed:

* Rune (critical entity) was omitted despite being active and referenced multiple times.

Root cause:

* Compression removed entities not recently repeated.
* No invariant enforcing entity persistence.

Conclusion:

> Narrative systems must be treated as **state machines**, not stories.

---

## 1.2 Entity Continuity is Non-Negotiable

Once an entity is introduced, it must persist until explicitly removed.

Failure observed:

* Rune introduced → omitted later
* Pregnancy state misaligned with children count

Correct rule:

> No entity may disappear unless an explicit terminal event exists (death, departure, merge).

---

## 1.3 Late-Stage Text is Not a Complete Snapshot

Later passages assume prior context and do not restate all facts.

Failure observed:

* Over-reliance on “status update” sections
* Earlier entities not re-mentioned → incorrectly dropped

Correct rule:

> Later text refines state; it does not redefine the full world.

---

## 1.4 Topic-Based Extraction is Insufficient

Extracting by theme (e.g., “settlement,” “defense”) misses cross-cutting entities.

Failure observed:

* Rune categorized as “biological detail” instead of:

  * entity
  * anomaly carrier
  * system influence vector

Correct rule:

> Extraction must be **entity-first**, not topic-first.

---

## 1.5 State Transitions Must Be Explicit

Events are not just narrative—they define system evolution.

Failure observed:

* Rune’s lifecycle collapsed into “birth arc”
* Lost:

  * birth conditions
  * monitoring policy
  * environmental resonance

Correct rule:

> Every meaningful change = explicit state transition.

---

## 1.6 Anomalies Are First-Class Data

Subtle effects (like Rune’s environmental influence) are not flavor—they are system signals.

Failure observed:

* Rune’s environmental ordering effect not preserved as system behavior

Correct rule:

> Any deviation from baseline must be tracked as an **anomaly object**.

---

## 1.7 No Validation Layer = Silent Corruption

Without validation, incorrect summaries appear plausible.

Failure observed:

* Missing child not detected

Correct rule:

> Every extraction pass must be followed by invariant checks.

---

# 2. Core Design Principles

1. **Entity persistence over narrative compression**
2. **Event-sourced state, not snapshot rewriting**
3. **Explicit modeling of anomalies and unknowns**
4. **Validation before output**
5. **Separation of storage vs presentation**

---

# 3. Extraction Specification

## 3.1 Data Model

> **Note:** This section was written outside the repo. Where the repo already defines
> a schema (see `schemas/`), the repo schema is canonical. Event and Anomaly schemas
> were added in issues #23 and #26. InvariantCheck is tracked as future work in
> issue #29.

### Entity

Represents any persistent actor or object.
See `schemas/entity.schema.json` for the canonical schema.

Fields:

* `id` — prefixed by type: `char-*`, `loc-*`, `faction-*`, `item-*`, `creature-*`, `concept-*`
* `name`
* `type` — one of: `character`, `location`, `faction`, `item`, `creature`, `concept`
* `description` — factual, from transcript
* `attributes` — key-value pairs (tag as inference where appropriate)
* `relationships` — inline array of `{ target_id, relationship, confidence? }`
* `first_seen_turn` — pattern: `turn-[0-9]{3,}`
* `last_updated_turn` — pattern: `turn-[0-9]{3,}`
* `notes`

---

### Relationship

Relationships are stored **inline** on each entity as `relationships[]`
entries (see `schemas/entity.schema.json`).

Each entry contains:

* `target_id` — ID of the related entity
* `relationship` — freeform label (e.g., parent, partner, leader)
* `type` — one of: `kinship`, `partnership`, `mentorship`, `political`, `factional`, `tribal_role`, `other`
* `direction` — optional: `outgoing`, `incoming`, or `bidirectional`
* `confidence` — optional, 0.0–1.0
* `first_seen_turn` — optional, pattern: `turn-[0-9]{3,}`
* `last_updated_turn` — optional, pattern: `turn-[0-9]{3,}`

---

### Event

Atomic change in the system.
See `schemas/event.schema.json` for the canonical schema.

Fields:

* `id` — pattern: `evt-[0-9]+`
* `source_turn` — turn where the event occurred (pattern: `turn-[0-9]{3,}`)
* `type` — one of: `birth`, `death`, `arrival`, `departure`, `construction`, `decision`, `encounter`, `recruitment`, `discovery`, `anomaly`, `other`
* `related_entities` — array of entity IDs
* `description`
* `related_threads` — optional, plot thread IDs
* `notes`

---

### State

Materialized view at a point in time.
See `schemas/state.schema.json` for the canonical schema.

Fields:

* `as_of_turn` — turn-anchored timestamp (pattern: `turn-[0-9]{3,}`)
* `current_world_state` — narrative summary (explicit facts only)
* `player_state` — `{ location, condition, inventory_notes, relationships_summary }`
* `known_constraints` — confirmed DM-stated limitations
* `inferred_constraints` — derived limitations with `{ statement, confidence, source_turns }`
* `opportunities` — available actions/paths
* `risks` — threats/dangers
* `active_threads` — active plot thread IDs

---

### Anomaly

Deviation from expected behavior.
See `schemas/anomaly.schema.json` for the canonical schema.

Fields:

* `id` — pattern: `anomaly-*`
* `category` — optional: `environmental`, `entity-linked`, `artifact-linked`, `system`
* `related_entities` — array of entity IDs (optional)
* `description`
* `first_seen_turn` — pattern: `turn-[0-9]{3,}`
* `last_updated_turn` — optional, pattern: `turn-[0-9]{3,}`
* `observation_turns` — optional, all turns where observed
* `trend` — one of: `stable`, `expanding`, `diminishing`, `unknown`
* `notes`

Example:

* char-rune → environmental ordering effect (frost/dust alignment)

---

### Prompt Candidate

Explicit player/DM branching moment.
See `schemas/prompt-candidate.schema.json` for the canonical schema.

Fields:

* `id` — pattern: `pc-[0-9]+`
* `recommendation_mode` — `desired_outcome`, `roleplay_consistent`, or `all_options`
* `style` — `safe`, `probing`, `aggressive`, `diplomatic`, `deceptive`, `exploratory`, `direct`
* `proposed_prompt` — exact player text
* `rationale`
* `expected_upside` / `risk`
* `objective_refs` — related objective IDs

---

### InvariantCheck *(not yet in repo — see issue #29)*

Validation rules applied after extraction. Currently `tools/validate.py`
checks JSON schema compliance only; completeness checks are proposed.

Proposed fields:

* `id`
* `rule_description`
* `validation_logic`
* `result` (pass/fail)

---

## 3.2 Extraction Pipeline

### Phase 1 — Raw Retrieval

* Chunk document into individual turns
* Retrieve all turns
* Preserve ordering (turn sequence numbers)

---

### Phase 2 — Entity Registration

For each turn:

* Extract all named entities
* Create or update entity records in `framework/catalogs/`

Rule:

> No deduplication by assumption—only by explicit identity match.

---

### Phase 3 — Event Extraction

Identify:

* births
* arrivals
* constructions
* decisions
* anomalies

Create explicit event records (with `source_turn` provenance).

---

### Phase 4 — Relationship Mapping

Link:

* parent-child
* partnerships
* leadership roles
* system roles

---

### Phase 5 — Anomaly Detection

Extract:

* environmental deviations
* behavioral shifts
* system instability

Attach to:

* entity OR system

---

### Phase 6 — State Assembly

Build current state (`derived/state.json`):

* all active entities (from `framework/catalogs/`)
* all active relationships (inline on entities)
* all active anomalies
* active plot threads

---

### Phase 7 — Validation

#### Required Checks

1. **Entity Persistence**

   * All previously active entities must still exist unless removed

2. **Family Consistency**

   * Children list must match:

     * births
     * pregnancy state

3. **Anomaly Attachment**

   * All anomalies must link to an entity or system

4. **Event Coverage**

   * Every major transition must have an event

5. **No Implicit Deletion**

   * Missing ≠ removed

---

### Phase 8 — Output Generation

Only after validation:

* generate turn summaries (`derived/turn-summary.md`)
* update state (`derived/state.json`)
* generate prompt candidates (`derived/prompt-candidates.json`)
* generate next-move analysis (`derived/next-move-analysis.md`)

---

# 4. Minimal Schema Example (JSON-like)

```json
{
  "id": "char-rune",
  "name": "Rune",
  "type": "character",
  "description": "Youngest child of Fenouille and Kael, exhibits environmental resonance",
  "attributes": { "environmental_resonance": true },
  "relationships": [
    { "target_id": "char-fenouille", "relationship": "parent" },
    { "target_id": "char-kael", "relationship": "parent" }
  ],
  "first_seen_turn": "turn-230",
  "last_updated_turn": "turn-340"
}
```

```
Anomaly (proposed):
  id: anomaly-rune-env
  related_entities: [char-rune]
  description: local environmental ordering (frost/dust alignment)
  first_seen_turn: turn-232
  trend: unknown

Evidence:
  id: ev-042
  statement: Rune born to Fenouille and Kael
  classification: explicit_evidence
  confidence: 1.0
  source_turns: [turn-230]
  related_entities: [char-rune, char-fenouille, char-kael]
```

---

# 5. Key Insight for the System

This is not:

* a story parser
* a summarizer

This is:

> A **persistent world-state compiler with anomaly tracking**

---

# 6. Immediate Improvements for Your Repo

1. Implement entity extraction into `framework/catalogs/` first (before any summarization) — see issue #22
2. Add invariant/completeness checks to `tools/validate.py` — see issue #29
3. Store events append-only (event sourcing) — see issue #23
4. Maintain separation of:

   * raw transcript (`sessions/*/raw/`, `sessions/*/transcript/`)
   * derived outputs (`sessions/*/derived/`)
5. Treat anomalies as primary signals, not edge cases — see issue #26

---

# 7. Final Principle

> If an entity can influence the system, it must exist in the model.
> If it exists in the model, it must not disappear without a trace.

Rune violated this rule—and exposed the flaw.

---
