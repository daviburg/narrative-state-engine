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

## 1.8 One Phase, One Responsibility — Ambiguous Templates Cause Schema Drops

A prompt template that is ambiguous about a field, or that invites a phase to
emit data another phase owns, silently drops whole entity updates when the
resulting object fails schema validation.

Failures observed (raw-IO capture, #505):

* `entity_detail` emitted `"notes": null` because the template documented
  `notes` as "(optional)" without saying to **omit** it when empty — the
  schema requires a string, so the whole entity update was dropped. Proof it
  was ambiguity, not capability: in the same turn the model wrote
  `"notes": ""` (valid) for another entity.
* `entity_detail` echoed back and **invented** `relationships` (e.g.
  `char-younger-woman`) without the required `first_seen_turn`, because the
  prior-catalog context contained relationships — even though relationships are
  owned by the separate `relationship_mapper` phase and `entity-detail.md`
  never documented the field.

Correct rules:

> Tell the model to **omit** optional fields when empty; never leave "emit null"
> as an implicit option.
>
> Give each phase **exactly one** responsibility. If context unavoidably leaks
> another phase's data (e.g. relationships in the prior-entity context), forbid
> re-emitting it in the template **and** strip it in the parser as a
> cause-independent net, so an echoed/invented value neither merges nor fails
> validation.

Fix (#505): `entity-detail.md` now instructs omit-`notes`-when-empty (never
`null`) and forbids a `relationships` array; `_coerce_entity_fields()` strips
any `relationships`/relationship-variant keys and a `null` `notes` from
`entity_detail` output. The `relationship_mapper` phase is untouched, so real
relationship data still flows through it.

---

## 1.9 Compound Personal Names Fragment Without an Explicit Coreference Rule

When a character is introduced by a full compound personal name (given name +
surname) and later referenced by only the given name *or* only the surname, the
discovery phase mints a **new** `char-*` id for each surface form. The same
person ends up split across two or three catalog ids that interval/end-of-run
dedup does not merge — the surface strings differ ("Mara", "Veylin", "Mara
Veylin"), so name-similarity heuristics see three distinct entities.

Failure observed (in an observed extraction, #524): roughly a 45%
character-fragmentation rate, with families like `char-mara` / `char-veylin` /
`char-mara-veylin` — a single person introduced as "Mara Veylin" split into
three ids when later referenced by the bare given name ("Mara") and the bare
surname ("Veylin").

Root cause: the `entity-discovery.md` coreference examples covered title/rank
changes, identity reveals, location aliases, group/subset, and *shortened
descriptive* names ("the elder shaman" → "the elder"), but **not** personal
given-name/surname components of a compound name. The model treated a bare
first or last name as a brand-new proper noun.

Correct rule (source-first, per Rule 10 — replicates the #443 coref-template
win, not a new post-processing sweep):

> A person's given name alone, their surname alone, and their full name are
> **one** entity. Before minting a new `char-*` id, check whether the proposed
> name shares a given-name or surname token with an existing character entity;
> if so, emit that `existing_id` (`is_new=false`) instead of a new
> `proposed_id`. The match is bidirectional: a bare component resolves to an
> existing full name, and a full name resolves to an existing component entity.

**Disambiguation guard (necessary but NOT sufficient).** A shared token is
required to merge, but it does not on its own prove identity. Three extra
conditions must hold before resolving to an existing character: (a) the shared
token matches **exactly one** existing character — if two or more existing
characters share it (e.g. two different "Mara"s), a bare reference is
*ambiguous*, so do not guess; (b) the proposed name introduces **no
conflicting second component** — a different surname paired with a matching
given name, or a different given name paired with a matching surname, marks a
**different person** (e.g. existing "Mara Veylin" vs. later "Joren Veylin" =
two distinct characters); and (c) the surrounding context supports
**continuity** with the existing character (a callback to someone already
present), **not a fresh introduction** of a new individual. First-introduction
language ("for the first time", "a stranger", "steps forward", "appears"), an
appositive that conflicts with the known character's role ("Mara, the baker"
when the known Mara is a guard), "another Mara", or "a young Veylin" all mark a
**new** person — mint a new id even though the bare name collides. When any
condition fails, or continuity is genuinely ambiguous, mint a new id rather
than over-merge.

**Precision/recall tradeoff (no overclaim of correctness).** This rule trades
recall for precision: it reduces fragmentation, but a bare name matching an
existing character is merged **only** when context continuity holds, and
first-introduction / distinct-role context overrides the merge (mint new). The
default bias is deliberately toward minting a new id when continuity is
uncertain — fragmentation is recoverable in a later dedup pass, whereas a false
merge corrupts identity and is far harder to undo. As a belt-and-suspenders
net, the #398 compound-fragment filter (`_is_compound_term_fragment`) is taught
to spare a bare-name callback **only when its `existing_id` resolves against the
real catalog id-set** (`_build_known_id_set` / `find_entity_by_id`). A bare
`is_new=false` with no resolvable id, or any unresolvable `existing_id`, does
**not** bypass the filter — it **fails closed**. More generally, every
discovery record carrying a non-empty `existing_id` is validated against the
catalog id-set by one reusable helper (`_validate_existing_ids`) applied at
**two** points: inside `_run_discovery_phase` (after compact expansion, before
any record becomes a detail/merge task) **and** again at the `extract_and_merge`
ingress on the prefetched `qualified` list (defense-in-depth for the batch
path; idempotent — a no-op on already-clean data). When an `existing_id` does
not resolve: if the record is an explicit brand-new entity (`is_new=true` with a
non-empty, prefix-valid, **non-colliding** `proposed_id`) the bogus reference is
cleared and the record proceeds as genuinely new; otherwise it is **dropped**
(logged as `unresolvable_existing_id`). The drop deliberately includes the
ambiguous **collision** case — an unresolvable `existing_id` whose `proposed_id`
duplicates a real catalog id — which **fails closed** rather than being
rerouted: proceed-as-new would reuse a colliding id (corrupts the matched
entity), and reroute-as-existing would false-merge/rename a possibly-different
entity (e.g. a genuinely-new "Mara Baker" carrying `proposed_id=char-mara-veylin`
could rename the catalogued "Mara Veylin", since the name guard only blocks
zero-overlap names), so dropping the rare malformed record — re-extractable next
turn — is the only safe choice absent deterministic identity proof. A record
with an unresolvable `existing_id` therefore never reaches a detail/merge task on
**either** the sequential or the batch (prefetched) path, so the detail model
can no longer be handed a fabricated "existing" id and `merge_entity` can no
longer append a bogus catalog entity.
The guard keys on id-resolution against the real catalog, not on a domain word
list (Rules 9 & 10). The discovery template instructs personal-name callbacks
to be emitted in the compact `{"existing_id": ..., "confidence": ...}`
known-entity form, which is expanded to the catalogued full name before
filtering.

Fix (#524): `entity-discovery.md` gains a `PERSONAL NAME COREFERENCE` rule (with
the three-part disambiguation guard, a negative "different person" example, and
a fresh-introduction "new person" example) plus a "Personal-name components"
example; `entity-detail.md` and `entity-detail-batch.md` instruct the detail
phase to canonicalize to the fullest name **only when it is the same person**
and record every shorter surface form in `stable_attributes.aliases`, so the
merger keeps the fragments collapsed and later turns resolve to the canonical
id. The `_is_compound_term_fragment` filter gains a known-reference skip so
bare-name callbacks survive. No new Python word list or tuned threshold was
added (Rules 9 & 10).

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
* `relationships` — inline array of `{ target_id, relationship, type, direction?, confidence?, first_seen_turn?, last_updated_turn? }`
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
* `source_turns` — turns where the event occurred or is evidenced (pattern: `turn-[0-9]{3,}`)
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
