# Catalog V2 Design — Entity State Model & Agent Context

> Design document for narrative-state-engine catalog restructuring.
> Status: **Draft** | Date: 2026-04-11

---

## 1. Executive Summary

After a 345-turn extraction run, the catalog system has proven structurally unable to serve its core purpose: giving the planning agent actionable entity state for next-move analysis. Relationships accumulate as unbounded per-turn observations with no consolidation or temporal validity — the PC has 37 relationships including 14 near-synonyms with a single NPC. Entity descriptions freeze at the turn they were first written rather than evolving with the narrative. Attributes are overwritten each turn with momentary observations instead of maintaining stable traits. The analysis agent (`analyze_next_move.py`) cannot consume catalogs at all because they are too large and too noisy; it currently works only from `state.json`, `evidence.json`, and `objectives.json`, ignoring all entity data. This design proposes: (1) consolidated per-pair relationships with temporal status tracking, (2) an identity/status split for entity descriptions, (3) stable vs. volatile attribute separation, (4) a per-entity file layout with a context-builder tool that produces focused per-turn entity context, (5) optional structured mechanical state fields, and (6) a one-time migration path from the current flat-file format.

---

## 2. Problem Analysis

### Problem 1: Catalogs Are Event Logs, Not Entity State

#### Current behavior

Every extraction pass appends a new relationship object for each interaction it observes in a turn. The schema has no mechanism to mark a relationship as resolved, superseded, or stale. Relationships accumulate indefinitely.

**Evidence from the 345-turn run:**

- `char-player` has **37 relationships** spanning turn-001 to turn-078.
- `"captured by" char-two-figures` (turn-007) coexists with `"befriends" char-young-hunter` (turn-052). The character was freed around turn-029, but nothing marks the capture as resolved.
- `char-two-figures` was last updated at turn-049 — a 29-turn gap to transcript end — yet all its relationships remain indistinguishable from active ones.

An analysis agent presented with this data cannot determine which relationships are current. It would have to guess based on turn numbers, which requires temporal reasoning the agent shouldn't need to perform.

#### Proposed solution

Add a `status` field to each relationship and track temporal validity explicitly.

```jsonc
// Relationship object — proposed
{
  "target_id": "char-two-figures",
  "relationship": "captured by",
  "type": "political",
  "direction": "incoming",
  "status": "resolved",           // NEW: active | dormant | resolved
  "confidence": 1.0,
  "first_seen_turn": "turn-007",
  "last_updated_turn": "turn-029",
  "resolved_turn": "turn-029",    // NEW: when status changed to resolved
  "resolution_note": "freed by tribe after integration period"  // NEW: optional
}
```

**Status definitions:**

| Status | Meaning | Agent interpretation |
|--------|---------|---------------------|
| `active` | Relationship is current and ongoing | Include in planning context |
| `dormant` | Entity hasn't appeared recently; relationship may still hold but is unconfirmed | Include if entity becomes relevant |
| `resolved` | Relationship has ended or been superseded | Exclude from active context; retain for history |

**Who sets status:**

- The extraction LLM sets `active` on new or updated relationships.
- The catalog merger marks relationships as `dormant` when the source or target entity hasn't appeared for N turns (configurable; suggested default: 10 turns).
- The extraction LLM can explicitly set `resolved` when a turn shows a relationship ending (e.g., "the prisoner is freed").
- The context builder filters by status when producing agent context.

#### Tradeoffs

| Approach | Pros | Cons |
|----------|------|------|
| **Status field (proposed)** | Clear agent filtering; preserves history; provenance intact | Requires LLM to understand status transitions; dormancy heuristic may misfire |
| Keep N most recent per target | Simple; auto-prunes | Loses provenance; can't distinguish "ended" from "old" |
| Aggregate per-target description | Compact; holistic | Loses individual provenance; hard to undo bad merges |
| Separate history from current | Clean agent view | Two data stores to maintain; sync risk |

**Recommendation:** Status field on each relationship. The dormancy auto-marking in catalog_merger provides a safety net when the LLM fails to explicitly resolve relationships.

---

### Problem 2: Entity Descriptions Are Single-Turn Snapshots

#### Current behavior

The `description` field is written when an entity is first extracted and occasionally overwritten on later turns. It captures the moment of extraction, not the entity's accumulated identity.

**Evidence:**

- `char-player` description: *"A player character who introduces themselves as Fenouille Moonwind, communicating with a young hunter in the morning."*
- By turn-078 the character has: established herself with the tribe, become a healer/herbalist, formed a close bond with the young hunter (Kael), become the tribe's de facto leader, become Kael's concubine, and is mother of 4 children (one unborn). **None of this is in the description.**
- Location descriptions suffer the same problem: `loc-campsite` is frozen as *"A central campfire with a lone hunter keeping watch"* (turn-049 snapshot) despite the camp evolving significantly.

The analysis agent receiving this description would have no understanding of who the PC actually is at the current point in the narrative.

#### Proposed solution

Split the single `description` field into two fields: `identity` (stable, rarely changes) and `current_status` (volatile, updated when the entity appears in a turn).

```jsonc
// Entity description fields — proposed
{
  "identity": "Fenouille Moonwind, an elf healer/herbalist and de facto leader of the wilderness tribe. Originally from a distant village. Known for communication skills in multiple languages and herbal knowledge.",
  "current_status": "Mother of 4 (one unborn). Living as Kael's concubine at the main camp. Currently organizing the tribe's midwinter preparations and overseeing herb gathering expeditions.",
  "identity_updated_turn": "turn-052",
  "status_updated_turn": "turn-078"
}
```

**Field definitions:**

| Field | Content | Update frequency | Max length guideline |
|-------|---------|-----------------|---------------------|
| `identity` | Who/what the entity is: name, race/type, role, defining traits, affiliations | Only when fundamental identity changes (new name revealed, role change, major transformation) | 2–3 sentences |
| `current_status` | What the entity is doing now: current activities, condition, immediate circumstances | Every turn the entity appears in | 1–3 sentences |

**Extraction template change:** The entity-detail template receives the prior `identity` and `current_status` fields. The LLM decides whether identity needs updating (usually not) and always updates current_status if the entity appeared in the turn.

#### Tradeoffs

| Approach | Pros | Cons |
|----------|------|------|
| **Identity + current_status split (proposed)** | Clear separation of stable vs. volatile; agent gets both perspectives; manageable LLM task | LLM must judge what's "identity" vs "status"; some entities blur the line |
| Holistic evolving description | Simpler schema; one field to maintain | LLM must synthesize entire history each time; quality degrades with length; hard to distinguish stable from transient |
| On-demand generation from events | Always fresh; no stale data | Requires LLM call at analysis time (latency); depends on event quality; no pre-computed state for agent |

**Recommendation:** Identity + current_status split. The extraction LLM is already reading prior entity data; asking it to maintain two focused fields is a modest additional ask that a 14B model can handle. On-demand generation is rejected because it adds latency at analysis time and makes the system dependent on event quality (which has its own issues).

---

### Problem 3: Attributes Are Overwritten Incoherently

#### Current behavior

The `attributes` object is a freeform `string → string` map. Each extraction pass writes whatever the LLM observes in that turn, with no distinction between stable traits and momentary observations.

**Evidence:**

```json
// char-player attributes (actual extraction output)
{
  "condition": "Seems expectant and clear in their communication gesture",
  "status": "Observing environmental changes and interacting with an unseen entity",
  "equipment": "lantern, possibly other survival gear, small carving tool with polished bone handle, staff [inference]",
  "hp_change": "-2 HP (from previous turn) -1 HP (from this encounter), +2 HP (restored)",
  "race": "Elf [inference]",
  "class": "Ranger [inference]",
  "appearance": "Words and actions suggest a thoughtful and observant demeanor."
}
```

**Problems:**

- `condition` and `status` capture single-turn observations, not persistent state.
- `equipment` mixes confirmed items with speculation ("possibly other survival gear").
- `hp_change` is a narrative string, not a usable mechanical value.
- `class: "Ranger [inference]"` and `appearance: "warlock attire"` (from another turn) contradict each other with no resolution.
- `appearance` describes behavior ("thoughtful and observant demeanor"), not physical appearance.

#### Proposed solution

Replace the freeform `attributes` object with two structured sections: `stable_attributes` (set once, updated rarely) and `volatile_state` (updated each turn the entity appears).

```jsonc
// Proposed attribute structure
{
  "stable_attributes": {
    "race": { "value": "Elf", "inference": true, "confidence": 0.7, "source_turn": "turn-003" },
    "class": { "value": "Ranger/Herbalist", "inference": true, "confidence": 0.5, "source_turn": "turn-012" },
    "appearance": { "value": "Lean build, carries herbal pouches and a staff", "inference": false, "source_turn": "turn-025" },
    "aliases": { "value": ["Player Character", "Fenouille", "Moonwind"], "inference": false, "source_turn": "turn-001" }
  },
  "volatile_state": {
    "condition": "Healthy, expecting fourth child",
    "equipment": ["lantern", "small carving tool with polished bone handle", "staff"],
    "location": "loc-camp-light",
    "last_updated_turn": "turn-078"
  }
}
```

**Stable attributes** have per-attribute provenance:

| Field | Type | Purpose |
|-------|------|---------|
| `value` | string or array | The attribute value |
| `inference` | boolean | Whether this is inferred (true) or explicit (false) |
| `confidence` | number (0.0–1.0) | Confidence score; omit for explicit facts |
| `source_turn` | string | Turn where this was established or last confirmed |

**Volatile state** is a flat object updated each turn, no per-field provenance (the `last_updated_turn` covers the whole block):

| Field | Type | Purpose |
|-------|------|---------|
| `condition` | string | Current physical/mental state |
| `equipment` | array of strings | Currently carried items |
| `location` | string | Current location entity ID (if known) |
| `last_updated_turn` | string | Turn this block was last updated |

**Extraction template change:** The entity-detail template receives prior stable_attributes and volatile_state. Instructions specify:
- Stable attributes: only update if the turn contains new definitive information (e.g., DM confirms race). Never overwrite with turn-specific observations.
- Volatile state: always update with current-turn observations. Equipment is a full current list, not a diff.

#### Tradeoffs

| Approach | Pros | Cons |
|----------|------|------|
| **Stable + volatile split (proposed)** | Prevents overwrite of stable traits; clear provenance; agent can load volatile-only for quick state | Requires LLM to categorize attributes; more complex schema |
| Append-only attribute history | Full temporal record; never loses data | Grows unboundedly; agent must scan history to find current values |
| Structured typed fields only | Strong schema validation; no ambiguity | Too rigid for unknown game systems; can't anticipate all attribute types |
| Keep freeform, just improve templates | Minimal schema change | Doesn't solve the fundamental overwrite problem; LLM behavior is unreliable without structural guardrails |

**Recommendation:** Stable + volatile split. This directly addresses the overwrite problem while keeping the system game-agnostic. The stable_attributes keys are not predefined — any key can be stable — but the entity-detail template provides guidance on which attributes are typically stable (race, class, appearance, role) vs. volatile (condition, equipment, status).

---

### Problem 4: Flat Catalog Files Waste Agent Context

#### Current behavior

Each entity type lives in a single flat JSON file (`characters.json`, `locations.json`, etc.). Loading `characters.json` means loading all entities with all their relationships — approximately 1,800 lines for 10 characters. The analysis agent template (`next-move-analysis.md`) loads `state.json`, `evidence.json`, `objectives.json`, and strategy files, but **does not load any catalog files** because they are too large and unstructured for productive agent consumption.

This means the analysis agent has **zero entity detail** when generating next-move recommendations. It knows "the elder" exists from state.json's narrative summary but doesn't know the elder's relationships, attributes, or recent activity.

#### Proposed solution — Option D: Hybrid (index + detail + context builder)

Three layers serve different purposes:

**Layer 1: Per-entity detail files (backing store)**

```
framework/catalogs/characters/
  index.json            → lightweight roster
  char-player.json      → full entity detail
  char-elder.json       → full entity detail
  char-young-hunter.json → full entity detail
  ...

framework/catalogs/locations/
  index.json
  loc-camp-light.json
  ...
```

Each per-entity file contains the full entity object (identity, current_status, stable_attributes, volatile_state, relationships). The `index.json` contains a lightweight summary array.

**Layer 2: Index files (entity roster)**

```jsonc
// framework/catalogs/characters/index.json
[
  {
    "id": "char-player",
    "name": "Fenouille Moonwind",
    "type": "character",
    "status_summary": "De facto tribe leader, healer, mother of 4. Active.",
    "last_updated_turn": "turn-078",
    "first_seen_turn": "turn-001",
    "active_relationship_count": 8
  },
  {
    "id": "char-elder",
    "name": "The Elder",
    "type": "character",
    "status_summary": "Tribal elder and authority figure. Active but less prominent recently.",
    "last_updated_turn": "turn-072",
    "first_seen_turn": "turn-016",
    "active_relationship_count": 5
  }
]
```

The index is cheap to load (~10–20 lines per entity, ~100-200 lines per type) and tells the agent or context builder which entities exist and their rough relevance.

**Layer 3: Context builder tool (agent-facing output)**

A new tool (`tools/build_context.py`) that, given a session and turn number:

1. Reads the latest turn transcript to identify mentioned entity IDs.
2. Loads index files to find those entities plus their relationship targets (one hop).
3. Loads per-entity detail files for the relevant subset.
4. Filters to active relationships only.
5. Produces `derived/turn-context.json` — a focused context document.

```jsonc
// sessions/session-001/derived/turn-context.json
{
  "as_of_turn": "turn-078",
  "scene_entities": [
    {
      "id": "char-player",
      "name": "Fenouille Moonwind",
      "identity": "Fenouille Moonwind, an elf healer/herbalist...",
      "current_status": "Mother of 4, organizing midwinter preparations...",
      "volatile_state": {
        "condition": "Healthy, expecting fourth child",
        "equipment": ["lantern", "carving tool", "staff"],
        "location": "loc-camp-light"
      },
      "active_relationships": [
        {
          "target_id": "char-young-hunter",
          "target_name": "Kael",
          "relationship": "intimate partner and co-parent",
          "type": "partnership",
          "status": "active"
        },
        {
          "target_id": "char-elder",
          "target_name": "The Elder",
          "relationship": "respected authority, seeks guidance from",
          "type": "political",
          "status": "active"
        }
      ]
    },
    {
      "id": "char-young-hunter",
      "name": "Kael",
      "identity": "Young hunter of the wilderness tribe...",
      "current_status": "Partner to Fenouille, father of their children...",
      "volatile_state": { "condition": "Active hunter", "location": "loc-camp-light" },
      "active_relationships": [
        {
          "target_id": "char-player",
          "target_name": "Fenouille Moonwind",
          "relationship": "intimate partner and co-parent",
          "type": "partnership",
          "status": "active"
        }
      ]
    }
  ],
  "scene_locations": [
    {
      "id": "loc-camp-light",
      "name": "The Camp",
      "identity": "Main tribal campsite centered around a large bonfire...",
      "current_status": "Active settlement with lean-tos around central fire."
    }
  ],
  "nearby_entities_summary": [
    { "id": "char-elder", "name": "The Elder", "status_summary": "Tribal authority figure. Last active turn-072." },
    { "id": "faction-women-at-camp", "name": "Women at Camp", "status_summary": "Collective group handling daily camp tasks." }
  ]
}
```

The `scene_entities` array contains full detail for entities directly involved in the current turn. The `nearby_entities_summary` array contains index-level summaries for entities that are related to scene entities but not directly present — giving the agent awareness without context bloat.

**Integration with `analyze_next_move.py`:** The analysis template adds `turn-context.json` to its context alongside `state.json`, `evidence.json`, and `objectives.json`. The template section might read:

```
## Entity Context (from turn-context.json)

The following entities are active in the current scene:
{{turn_context.scene_entities}}

Other nearby or recently relevant entities:
{{turn_context.nearby_entities_summary}}
```

#### Tradeoffs

| Approach | Pros | Cons |
|----------|------|------|
| **Hybrid: per-entity files + context builder (proposed)** | Agent gets focused context; catalogs remain complete for reference; file-based and git-friendly; minimal context waste | Three layers to maintain; context builder adds a pipeline step; per-entity files create many small files |
| Option A only (index + detail split) | Simpler than hybrid; agent can selectively load | Agent still must decide what to load; no automated relevance filtering |
| Option B only (layered state model) | Active-entities view is clean | Who builds the active-entities view? Still need entity selection logic. |
| Option C only (context builder) | Agent gets perfect context | If catalogs stay flat, the builder must parse large files; no benefit when browsing manually |

**Recommendation:** Hybrid (Option D). The per-entity file split makes catalogs browsable and git-diff-friendly. The context builder automates relevance filtering so the analysis agent doesn't waste context on irrelevant entities. The index files provide a fast overview without loading full detail.

**File count concern:** A 50-entity campaign would produce ~50 entity files + 4 index files + 1 turn-context file per session. This is manageable for git and filesystem alike. Deeply nested paths are avoided — the directory tree is only one level deep within each catalog type.

---

### Problem 5: Relationship Accumulation Without Consolidation

#### Current behavior

The extraction pipeline creates a new relationship object every time it observes an interaction between two entities. There is no consolidation. The deduplication in `catalog_merger.py` deduplicates by `(target_id, relationship)` pair, but since the LLM produces different `relationship` strings each turn ("assists", "befriends", "offers companionship"), every variant is treated as unique.

**Evidence:**

`char-player` → `char-young-hunter` has **14 separate relationship entries:**

| Turn | Relationship string | Type |
|------|-------------------|------|
| turn-050 | "assists" | partnership |
| turn-051 | "offers companionship to" | social ❌ |
| turn-052 | "befriends" | partnership |
| turn-053 | "friendship with" | partnership |
| turn-054 | "leaning on" | physical_contact ❌ |
| turn-055 | "relying on for comfort and support" | partnership |
| turn-058 | "communicates with" | other |
| turn-058 | "introduces self to" | other |
| turn-066 | "wishes to meet" | other |
| turn-069 | "observes" | other |
| turn-070 | "brings food to" | other |
| turn-074 | "serves" | other |
| turn-075 | "serves meal to" | other |
| turn-078 | "wants to support" | partnership |

These are not 14 different relationships. They are the evolution of one relationship from strangers to intimate partners. Two also have invalid relationship types (`social`, `physical_contact`) not in the schema enum.

#### Proposed solution

**One consolidated relationship per (source, target) entity pair.** The relationship evolves over time, with history preserved in a sub-array.

```jsonc
// Consolidated relationship — proposed
{
  "target_id": "char-young-hunter",
  "current_relationship": "intimate partner and co-parent",
  "type": "partnership",
  "direction": "bidirectional",
  "status": "active",
  "confidence": 1.0,
  "first_seen_turn": "turn-050",
  "last_updated_turn": "turn-078",
  "history": [
    { "turn": "turn-050", "description": "assists during hunt" },
    { "turn": "turn-052", "description": "friendship forming" },
    { "turn": "turn-055", "description": "emotional reliance developing" },
    { "turn": "turn-070", "description": "domestic partnership evident" },
    { "turn": "turn-078", "description": "intimate partner, co-parent of 4 children" }
  ]
}
```

**Key changes:**

1. **`current_relationship`** replaces the old `relationship` field. It describes the relationship *right now*, not at a single historical point.
2. **`history`** is an append-only array of significant changes. Not every turn needs an entry — only turns where the relationship meaningfully changed. This preserves provenance without unbounded growth.
3. **One record per (source, target) pair.** The extraction LLM receives the prior relationship object and outputs an updated version, not a new object.

**Extraction template change:** The relationship-mapper template receives existing relationships for all entity pairs involved in the current turn. For each pair, the LLM either:
- Updates `current_relationship` and appends a history entry if the relationship changed.
- Leaves the relationship unchanged if the turn doesn't modify it.
- Creates a new relationship record if none exists for this entity pair.

**Dedup simplification:** With one-per-pair semantics, dedup becomes `(source_id, target_id)` matching. No need for fuzzy string comparison of relationship descriptions.

#### Tradeoffs

| Approach | Pros | Cons |
|----------|------|------|
| **Consolidated per-pair (proposed)** | Eliminates accumulation; clear current state; history preserved; simple dedup | LLM must synthesize from prior state; history array can still grow (but slowly); requires migration of existing relationships |
| Structured progression model (stranger → acquaintance → ally → intimate) | Compact; progression is clear | Too rigid; not all relationships follow a linear progression (e.g., "captured by" → "freed" → "ally"); game-system assumption |
| Separate current from history files | Clean current view | Two files per entity; sync risk; still need consolidation logic |

**Recommendation:** Consolidated per-pair with free-text `current_relationship`. The optional history array provides provenance without requiring a rigid progression model. The progression concept can be layered on later as a derived field if desired.

**14B LLM feasibility:** The relationship-mapper template already receives entity IDs and turn text. Adding the prior relationship object for each pair is a modest context increase. The instruction "update this relationship or create a new one" is simpler than "describe all relationships you observe", which should *reduce* extraction errors.

---

### Problem 6: No Structured Mechanical State Tracking

#### Current behavior

Mechanical game state (HP, inventory, status effects) is tracked as freeform strings in `state.json` under `player_state`. There are no typed fields, no numeric values, and no structured arrays.

**Evidence:**

```json
{
  "player_state": {
    "location": "The main camp near the bonfire",
    "condition": "Healthy overall, minor fatigue",
    "inventory_notes": "lantern, small carving tool with polished bone handle, staff, herbal pouches",
    "relationships_summary": "Close bond with Kael (young hunter), respected by elder, integrated with tribe"
  }
}
```

- `inventory_notes` is a comma-separated string. No item IDs referencing the items catalog. No quantities. No distinction between carried and stored items.
- HP changes appear in the entity's `hp_change` attribute as a narrative string: `"-2 HP (from previous turn) -1 HP (from this encounter), +2 HP (restored)"`.
- No status effects tracking (poisoned, blessed, exhausted, etc.).

#### Proposed solution

Add **optional** structured fields to `player_state` for common mechanical state. These fields are game-system-agnostic — they use narrative descriptions, not numeric systems.

```jsonc
// state.json player_state — proposed extensions
{
  "player_state": {
    "location": "loc-camp-light",      // CHANGE: entity ID reference instead of freeform
    "condition": "Healthy, expecting fourth child",
    "hp": {                             // NEW: optional structured HP
      "narrative": "Full health, no injuries",
      "numeric": null,                  // null when game doesn't use numeric HP
      "last_change": {
        "delta": "+2",
        "source": "herbal treatment",
        "turn": "turn-065"
      }
    },
    "inventory": [                      // NEW: structured inventory
      {
        "item_id": "item-carving-tool",
        "name": "small carving tool with polished bone handle",
        "carried": true,
        "quantity": 1,
        "notes": null
      },
      {
        "item_id": null,                // null when item isn't in catalog
        "name": "herbal pouches",
        "carried": true,
        "quantity": 1,
        "notes": "various dried herbs for healing"
      }
    ],
    "status_effects": [],               // NEW: empty when no effects active
    "relationships_summary": "Close bond with Kael (young hunter), respected by elder, integrated with tribe"
  }
}
```

**Design principles:**

1. **All new fields are optional.** The schema uses `"required": ["location", "condition"]` — only the existing required fields remain required.
2. **`hp.numeric` is nullable.** Games without numeric HP set it to `null`; the `narrative` field always works.
3. **`inventory` items optionally reference catalog IDs.** When an item exists in the items catalog, `item_id` links to it. When it doesn't (e.g., generic "herbal pouches"), `item_id` is null and `name` stands alone.
4. **`status_effects` is an array of objects:** `{ "effect": "fatigued", "source": "long march", "since_turn": "turn-070" }`. Empty array when no effects are active.
5. **`location` changes to an entity ID** (`loc-camp-light`) when the location is in the catalog, or remains a freeform string when the location is too minor for catalog entry. The schema allows both.

#### Tradeoffs

| Approach | Pros | Cons |
|----------|------|------|
| **Optional structured fields (proposed)** | Structured data for tools/agents; graceful fallback to narrative; cross-references catalogs | More complex schema; extraction must produce structured output; increases LLM task complexity |
| Purely narrative (current approach, improved) | Simplest; no schema changes; 14B LLM handles easily | Agent can't programmatically reason about HP, inventory, or status; parsing narrative strings is fragile |
| Full numeric mechanical tracking | Most precise; enables automated calculations | Game-system-specific; brittle across different RPG systems; 14B LLM may struggle with numeric accuracy |

**Recommendation:** Optional structured fields with narrative fallback. The `hp.numeric` nullable pattern lets the engine work for games with or without numeric HP. The `inventory` array with optional `item_id` links provides structure without requiring every item to be cataloged.

---

## 3. Proposed Schema Changes

### 3.1 Revised Entity Schema

Changes from the current `entity.schema.json`:

```jsonc
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Entity (V2)",
  "type": "object",
  "required": ["id", "name", "type", "identity", "first_seen_turn"],
  "additionalProperties": false,
  "properties": {
    "id": {
      "type": "string",
      "pattern": "^(char|loc|faction|item|creature|concept)-[a-z0-9]+(-[a-z0-9]+)*$"
    },
    "name": { "type": "string", "minLength": 1 },
    "type": {
      "type": "string",
      "enum": ["character", "location", "faction", "item", "creature", "concept"]
    },

    // CHANGED: replaces single "description" field
    "identity": {
      "type": "string",
      "description": "Stable identity summary: who/what the entity is. 2-3 sentences."
    },
    "current_status": {
      "type": "string",
      "description": "Volatile status: what the entity is doing/experiencing now. 1-3 sentences."
    },
    "status_updated_turn": {
      "type": "string",
      "pattern": "^turn-[0-9]{3,}$",
      "description": "Turn when current_status was last updated."
    },

    // CHANGED: replaces freeform "attributes" object
    "stable_attributes": {
      "type": "object",
      "description": "Persistent traits (race, class, appearance, role). Rarely change.",
      "additionalProperties": {
        "type": "object",
        "required": ["value"],
        "properties": {
          "value": {
            "oneOf": [
              { "type": "string" },
              { "type": "array", "items": { "type": "string" } }
            ]
          },
          "inference": { "type": "boolean", "default": false },
          "confidence": { "type": "number", "minimum": 0.0, "maximum": 1.0 },
          "source_turn": { "type": "string", "pattern": "^turn-[0-9]{3,}$" }
        },
        "additionalProperties": false
      }
    },

    // NEW: volatile state block
    "volatile_state": {
      "type": "object",
      "description": "Current state: condition, equipment, location. Updated each turn entity appears.",
      "properties": {
        "condition": { "type": "string" },
        "equipment": { "type": "array", "items": { "type": "string" } },
        "location": { "type": "string" },
        "last_updated_turn": { "type": "string", "pattern": "^turn-[0-9]{3,}$" }
      },
      "additionalProperties": true
    },

    // CHANGED: relationship model (consolidated per-pair)
    "relationships": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["target_id", "current_relationship", "type", "first_seen_turn"],
        "additionalProperties": false,
        "properties": {
          "target_id": { "type": "string" },
          "current_relationship": {
            "type": "string",
            "description": "Current state of the relationship."
          },
          "type": {
            "type": "string",
            "enum": ["kinship", "partnership", "mentorship", "political", "factional", "tribal_role", "other"]
          },
          "direction": {
            "type": "string",
            "enum": ["outgoing", "incoming", "bidirectional"]
          },
          "status": {
            "type": "string",
            "enum": ["active", "dormant", "resolved"],
            "default": "active"
          },
          "confidence": { "type": "number", "minimum": 0.0, "maximum": 1.0 },
          "first_seen_turn": { "type": "string", "pattern": "^turn-[0-9]{3,}$" },
          "last_updated_turn": { "type": "string", "pattern": "^turn-[0-9]{3,}$" },
          "resolved_turn": { "type": "string", "pattern": "^turn-[0-9]{3,}$" },
          "resolution_note": { "type": "string" },
          "history": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["turn", "description"],
              "properties": {
                "turn": { "type": "string", "pattern": "^turn-[0-9]{3,}$" },
                "description": { "type": "string" }
              },
              "additionalProperties": false
            }
          }
        }
      }
    },

    "first_seen_turn": { "type": "string", "pattern": "^turn-[0-9]{3,}$" },
    "last_updated_turn": { "type": "string", "pattern": "^turn-[0-9]{3,}$" },
    "notes": { "type": "string" }
  }
}
```

**Summary of changes from V1:**

| V1 field | V2 field | Change type |
|----------|----------|-------------|
| `description` | `identity` + `current_status` + `status_updated_turn` | Split |
| `attributes` (freeform k/v) | `stable_attributes` (typed objects) + `volatile_state` | Split + structure |
| `relationships[].relationship` | `relationships[].current_relationship` | Renamed (semantic shift) |
| — | `relationships[].status` | Added |
| — | `relationships[].resolved_turn` | Added |
| — | `relationships[].resolution_note` | Added |
| — | `relationships[].history` | Added |

### 3.2 Index File Schema

```jsonc
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Entity Index",
  "type": "array",
  "items": {
    "type": "object",
    "required": ["id", "name", "type", "first_seen_turn"],
    "additionalProperties": false,
    "properties": {
      "id": { "type": "string" },
      "name": { "type": "string" },
      "type": {
        "type": "string",
        "enum": ["character", "location", "faction", "item", "creature", "concept"]
      },
      "status_summary": {
        "type": "string",
        "description": "One-line summary of current status. Derived from entity current_status."
      },
      "first_seen_turn": { "type": "string", "pattern": "^turn-[0-9]{3,}$" },
      "last_updated_turn": { "type": "string", "pattern": "^turn-[0-9]{3,}$" },
      "active_relationship_count": {
        "type": "integer",
        "minimum": 0,
        "description": "Number of relationships with status=active."
      }
    }
  }
}
```

**Index file generation:** The index is regenerated from per-entity files by the catalog merger after each extraction. It is a derived artifact, not a source of truth.

### 3.3 Turn Context Schema

```jsonc
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Turn Context",
  "description": "Focused entity context for a specific turn, consumed by the analysis agent.",
  "type": "object",
  "required": ["as_of_turn", "scene_entities"],
  "additionalProperties": false,
  "properties": {
    "as_of_turn": { "type": "string", "pattern": "^turn-[0-9]{3,}$" },

    "scene_entities": {
      "type": "array",
      "description": "Entities directly involved in the current turn. Full detail.",
      "items": {
        "type": "object",
        "required": ["id", "name", "identity"],
        "properties": {
          "id": { "type": "string" },
          "name": { "type": "string" },
          "identity": { "type": "string" },
          "current_status": { "type": "string" },
          "volatile_state": { "type": "object" },
          "active_relationships": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["target_id", "relationship"],
              "properties": {
                "target_id": { "type": "string" },
                "target_name": { "type": "string" },
                "relationship": { "type": "string" },
                "type": { "type": "string" },
                "status": { "type": "string" }
              }
            }
          }
        }
      }
    },

    "scene_locations": {
      "type": "array",
      "description": "Locations relevant to the current turn.",
      "items": {
        "type": "object",
        "required": ["id", "name"],
        "properties": {
          "id": { "type": "string" },
          "name": { "type": "string" },
          "identity": { "type": "string" },
          "current_status": { "type": "string" }
        }
      }
    },

    "nearby_entities_summary": {
      "type": "array",
      "description": "Related entities not directly in the scene. Index-level detail only.",
      "items": {
        "type": "object",
        "required": ["id", "name"],
        "properties": {
          "id": { "type": "string" },
          "name": { "type": "string" },
          "status_summary": { "type": "string" }
        }
      }
    }
  }
}
```

### 3.4 State Schema Changes

Additions to `state.schema.json` `player_state`:

```jsonc
// player_state — proposed additions (all optional)
{
  "hp": {
    "type": "object",
    "properties": {
      "narrative": { "type": "string", "description": "Prose description of health state." },
      "numeric": {
        "oneOf": [
          { "type": "integer" },
          { "type": "null" }
        ],
        "description": "Numeric HP value. Null for non-numeric games."
      },
      "max_hp": {
        "oneOf": [
          { "type": "integer" },
          { "type": "null" }
        ]
      },
      "last_change": {
        "type": "object",
        "properties": {
          "delta": { "type": "string" },
          "source": { "type": "string" },
          "turn": { "type": "string", "pattern": "^turn-[0-9]{3,}$" }
        }
      }
    }
  },
  "inventory": {
    "type": "array",
    "items": {
      "type": "object",
      "required": ["name"],
      "properties": {
        "item_id": {
          "oneOf": [
            { "type": "string" },
            { "type": "null" }
          ],
          "description": "Reference to items catalog. Null if not cataloged."
        },
        "name": { "type": "string" },
        "carried": { "type": "boolean", "default": true },
        "quantity": { "type": "integer", "minimum": 1, "default": 1 },
        "notes": {
          "oneOf": [
            { "type": "string" },
            { "type": "null" }
          ]
        }
      }
    }
  },
  "status_effects": {
    "type": "array",
    "items": {
      "type": "object",
      "required": ["effect"],
      "properties": {
        "effect": { "type": "string" },
        "source": { "type": "string" },
        "since_turn": { "type": "string", "pattern": "^turn-[0-9]{3,}$" }
      }
    }
  }
}
```

---

## 4. Impact on Existing Tools

### 4.1 `tools/catalog_merger.py` — **Major refactor**

| Change | Scope |
|--------|-------|
| Read/write per-entity files instead of flat arrays | Core I/O rewrite |
| Generate index.json files after each merge | New functionality |
| Relationship consolidation: merge by (source_id, target_id) pair, update `current_relationship` | Dedup logic rewrite |
| Dormancy marking: set `status: dormant` on relationships where neither entity appeared for N turns | New post-merge pass |
| Type-to-directory mapping replaces type-to-file mapping | Configuration change |
| Support both V1 (flat) and V2 (per-entity) formats during migration | Temporary compatibility layer |

### 4.2 `tools/semantic_extraction.py` — **Moderate changes**

| Change | Scope |
|--------|-------|
| Entity-detail template: provide prior `identity`, `current_status`, `stable_attributes`, `volatile_state` | Template + context assembly |
| Relationship-mapper template: provide existing relationships per entity pair, instruct update-not-append | Template + context assembly |
| Parse LLM output into V2 entity structure (identity/status split, stable/volatile attributes) | Output parsing |
| Write per-entity files instead of flat catalog arrays | I/O change (delegates to catalog_merger) |

### 4.3 `tools/analyze_next_move.py` — **Moderate changes**

| Change | Scope |
|--------|-------|
| Load `turn-context.json` alongside state.json, evidence.json, objectives.json | Context assembly |
| Update `next-move-analysis.md` template to include entity context section | Template revision |
| Optionally trigger `build_context.py` if turn-context.json is stale or missing | Pipeline orchestration |

### 4.4 `tools/build_context.py` — **New tool**

| Capability | Description |
|------------|-------------|
| Read latest turn transcript | Identify entity mentions by ID or name |
| Load entity indexes | Find relevant entities + one-hop relationship targets |
| Load per-entity detail files | Get full detail for scene-relevant entities |
| Filter active relationships | Exclude dormant/resolved relationships from agent context |
| Produce `turn-context.json` | Write focused context to session derived directory |

### 4.5 `templates/extraction/entity-detail.md` — **Template revision**

| Change | Description |
|--------|-------------|
| Output format | `identity` + `current_status` instead of `description` |
| Attribute format | `stable_attributes` + `volatile_state` instead of flat `attributes` |
| Merge instructions | Explicit guidance: "update identity only if fundamental change; always update current_status" |
| Prior state input | Template receives full prior entity for context |

### 4.6 `templates/extraction/relationship-mapper.md` — **Template revision**

| Change | Description |
|--------|-------------|
| Input change | Receives existing relationships per entity pair |
| Output format | Update existing relationship or create new one; not append-only |
| Status field | LLM can set `status: resolved` when a relationship ends |
| History | LLM appends history entry when relationship meaningfully changes |
| Dedup | One record per (source, target) pair enforced by template instructions |

### 4.7 `tools/validate.py` — **Schema updates**

| Change | Description |
|--------|-------------|
| Validate V2 entity schema | New schema file or updated existing |
| Validate per-entity files | Walk directory tree instead of loading single flat file |
| Validate index files | New schema |
| Validate turn-context.json | New schema |
| Validate state.json with new player_state extensions | Updated schema |

### 4.8 `tools/extract_structured_data.py` — **Minimal changes**

| Change | Description |
|--------|-------------|
| Write structured HP/inventory to state.json | Update player_state with `hp`, `inventory`, `status_effects` objects instead of freeform strings |

---

## 5. Migration Path

### Phase 1: Schema migration (one-time script)

Write `tools/migrate_catalogs_v2.py` that:

1. **Reads existing flat catalog files** (`characters.json`, `locations.json`, etc.).
2. **Splits each entity into a per-entity file:**
   - `framework/catalogs/characters/char-player.json`
   - `framework/catalogs/locations/loc-camp-light.json`
   - etc.
3. **Converts `description` → `identity` + `current_status`:**
   - `identity`: copy existing description (imperfect, but preserves data).
   - `current_status`: set to `"Status unknown — migrated from V1 catalog."` (requires LLM pass or manual update to populate properly).
4. **Converts `attributes` → `stable_attributes` + `volatile_state`:**
   - Known stable keys (`race`, `class`, `appearance`, `role`, `aliases`): move to `stable_attributes` with `inference: true/false` parsed from `[inference]` tag.
   - Known volatile keys (`condition`, `status`, `equipment`, `hp_change`): move to `volatile_state`.
   - Unknown keys: default to `stable_attributes`.
5. **Consolidates relationships per (source, target) pair:**
   - For each pair, keeps the most recent `relationship` string as `current_relationship`.
   - Moves all others to `history` array.
   - Sets `status: active` for relationships from the last 10 turns; `dormant` for older.
6. **Generates `index.json` files** for each entity type.
7. **Preserves original flat files** as `characters.v1.json` etc. (backup, not delete).

### Phase 2: Template updates

Update extraction templates to produce V2 output. Old templates remain available for sessions that haven't migrated.

### Phase 3: Tool updates

Update `catalog_merger.py`, `semantic_extraction.py`, and `analyze_next_move.py` to work with V2 format. Add format detection: if per-entity directory exists, use V2; if flat file exists, use V1. This allows gradual migration.

### Phase 4: LLM re-extraction (optional)

For best quality, re-run extraction on the full transcript with V2 templates. The one-time migration produces structurally correct but content-imperfect data (especially `identity` and `current_status`). A full re-extraction produces holistic descriptions.

### What doesn't need to change

- `sessions/*/raw/` and `sessions/*/transcript/` — immutable, untouched.
- `sessions/*/derived/state.json` — gains optional new fields, but existing fields remain valid.
- `sessions/*/derived/evidence.json` — no changes.
- `framework/objectives/objectives.json` — no changes.
- `framework/dm-profile/dm-profile.json` — no changes.
- `framework/story/` — no changes.

---

## 6. Open Questions

### Q1: Dormancy threshold

How many turns of inactivity should trigger automatic `dormant` status on relationships? The proposed default is 10 turns, but this may need tuning.

- Too low: entities mentioned every 15 turns get incorrectly marked dormant.
- Too high: stale relationships pollute the active context.
- Alternative: instead of a fixed threshold, use a percentage of total transcript length (e.g., 15% of turns).

**Needs:** User decision or empirical testing with the 345-turn dataset.

### Q2: Identity update trigger

When should the extraction LLM update the `identity` field vs. only updating `current_status`? The template can provide guidelines ("update identity only for fundamental changes: new name, role change, major transformation"), but the LLM may over- or under-update.

- Option A: Let the LLM decide every turn (risk: overwrites stable identity with turn-specific observations).
- Option B: Only update identity on explicit user request or every N turns.
- Option C: Template strictly says "never change identity unless..." with specific triggers.

**Recommendation:** Option C, but needs a concrete trigger list.

### Q3: Context builder entity selection

How should `build_context.py` determine which entities are "scene-relevant"? Options:

- **Name/ID matching:** scan turn text for entity names and IDs.
- **Relationship hop:** include all entities with active relationships to mentioned entities (one hop).
- **Recency:** include all entities updated in the last N turns.
- **Combination:** name matching + one relationship hop + recency filter.

**Recommendation:** Name matching + one hop for scene_entities; recency-filtered index entries for nearby_entities_summary. Needs validation with real turns.

### Q4: History array growth limit

Should the `history` array on consolidated relationships have a maximum length? Options:

- Unlimited (grows with campaign, but slowly — only meaningful changes, not every turn).
- Capped at N entries (e.g., 10), dropping oldest.
- Summarized: when exceeding N entries, LLM summarizes oldest entries into a single "early history" entry.

**Recommendation:** Start unlimited; revisit if campaigns exceed 500 turns and history arrays become unwieldy.

### Q5: Relationship type enum expansion

The current enum (`kinship`, `partnership`, `mentorship`, `political`, `factional`, `tribal_role`, `other`) maps poorly to some observed relationships. The extraction LLM invented `social`, `physical_contact`, and `service` — all defaulted to `other` by coercion.

Should the enum be expanded? Candidates:
- `social` (friendship, companionship)
- `service` (serves, provides for)
- `adversarial` (captured by, threatened by)
- `romantic` (intimate partner)

**Recommendation:** Add `social`, `adversarial`, and `romantic`. Drop `tribal_role` (too setting-specific; use `factional` or `political` instead). `service` maps to `partnership` or `other`.

### Q6: Per-entity file naming

Should per-entity files use the entity ID directly (`char-player.json`) or a sanitized slug? Entity IDs are already slug-safe by schema (`^(char|loc|faction|item|creature|concept)-[a-z0-9]+(-[a-z0-9]+)*$`), so ID-as-filename works. But if IDs contain many segments (e.g., `char-elder-of-the-northern-tribe`), filenames get long.

**Recommendation:** Use entity ID as filename. The ID pattern already constrains to filesystem-safe characters. Long names are acceptable.

---

## 7. Prioritized Implementation Plan

### P0 — Critical (do first; unblocks agent consumption)

| Item | Problem | Effort estimate | Dependencies |
|------|---------|----------------|--------------|
| 1. Relationship consolidation model | P1 + P5 | Medium | Schema change |
| 2. Identity/status description split | P2 | Medium | Schema change |
| 3. Entity schema V2 + validation | All | Medium | Items 1–2 design |
| 4. Migration script (V1 → V2) | All | Medium | Item 3 |

**Rationale:** Without consolidated relationships and holistic descriptions, the catalog data is unusable regardless of how it's stored or served. These changes fix the data model itself.

### P1 — Important (enables agent consumption of entity data)

| Item | Problem | Effort estimate | Dependencies |
|------|---------|----------------|--------------|
| 5. Stable/volatile attribute split | P3 | Low–Medium | Item 3 |
| 6. Per-entity file layout + index generation | P4 storage | Medium | Item 3 |
| 7. Context builder tool | P4 consumption | Medium | Item 6 |
| 8. Extraction template revisions (entity-detail + relationship-mapper) | P1–P5 | Medium | Items 1–2, 5 |
| 9. catalog_merger V2 update | P4 | Medium–High | Items 1, 5, 6 |

**Rationale:** These items bridge the gap between "fixed data model" and "agent can actually use entity data." The context builder is the critical new capability — it turns catalogs from a reference archive into an agent input.

### P2 — Desirable (quality-of-life improvements)

| Item | Problem | Effort estimate | Dependencies |
|------|---------|----------------|--------------|
| 10. Structured mechanical state (HP, inventory, status effects) | P6 | Low | Item 3 |
| 11. analyze_next_move.py integration with turn-context.json | P4 | Low | Item 7 |
| 12. Full re-extraction with V2 templates | All | Compute time only | Items 8–9 |
| 13. Relationship type enum revision | P5 | Low | Item 3 |

**Rationale:** These improve quality but aren't blocking. The analysis agent can start consuming entity data after P1 is complete; P2 refines the experience.

### Suggested implementation order

```
P0-1: Entity schema V2 (draft + validate)
P0-2: Relationship consolidation model (schema + merge logic design)
P0-3: Identity/status split (schema + template guidance)
P0-4: Migration script
  — Milestone: V2 catalog data exists and validates —
P1-5: Per-entity file layout + index generation
P1-6: Stable/volatile attribute split
P1-7: Extraction template revisions
P1-8: catalog_merger V2
P1-9: Context builder tool
  — Milestone: `analyze_next_move` can consume entity context —
P2-10: Structured mechanical state
P2-11: analyze_next_move integration
P2-12: Full re-extraction (optional)
P2-13: Relationship type enum revision
```

Each milestone produces a testable, self-contained state. The V1 format remains supported until all sessions are migrated.
