# Architecture

## Overview

The narrative-state-engine is a player-side assistant framework organized around a strict separation between **raw** (immutable) data and **derived** (reproducible) data.

```
External AI DM  <-->  Player  <-->  narrative-state-engine
```

The system never interacts with the AI DM directly. The player copies DM outputs and player prompts into the repository.

---

## Data Flow

```
DM response text
      |
      v
sessions/*/raw/full-transcript.md        (immutable append)
sessions/*/transcript/turn-NNN-dm.md     (immutable append)
      |
      +------------------------------------------+
      |                                          |
      v                                          v
tools/update_state.py                  tools/semantic_extraction.py  (optional, LLM)
      |                                          |
      +---> sessions/*/derived/state.json        +--- single-pass (default)
      +---> sessions/*/derived/objectives.json   |     +---> framework/catalogs/*.json
      +---> sessions/*/derived/evidence.json     |
      +---> sessions/*/derived/turn-summary.md   +--- segmented (--segment-size N)
      |                                          |     +---> segment 1..K catalogs (fresh)
      |                                          |     +---> reconcile_segments()
      |                                          |     +---> framework/catalogs/*.json
      |                                          |
      v                                          +---> framework/catalogs/events.json
      v                                          |
      +------------------------------------------+
      |
      v
tools/analyze_next_move.py
      |
      +---> sessions/*/derived/next-move-analysis.md
      +---> sessions/*/derived/prompt-candidates.json
```

Semantic extraction is triggered automatically during `bootstrap_session.py` (batch)
or via the `--extract` flag on `ingest_turn.py` (incremental). It requires an LLM
endpoint configured in `config/llm.json` and gracefully degrades if unavailable.

---

## Layer Descriptions

### Raw Layer (Immutable)

All original text is stored verbatim and never modified.

- `sessions/*/raw/full-transcript.md` — complete session transcript in order
- `sessions/*/transcript/turn-NNN-{player,dm}.md` — one file per turn

### Catalog Layer (Framework)

Running catalogs of entities, locations, factions, items, and plot threads extracted from all sessions.

- Updated after each turn
- Each entry includes `first_seen_turn` and `last_updated_turn` for traceability
- Catalogs grow over time; entries are never deleted

### State Layer (Derived, Per Session)

Per-session structured state extracted from the transcript.

- `state.json` — current world state, player state, constraints, opportunities, risks
- `objectives.json` — current player objectives with status
- `evidence.json` — tagged evidence (explicit, inference, bait, hypothesis)

### Analysis Layer (Derived, Per Turn)

Generated after each new DM turn.

- `turn-summary.md` — concise summary of the latest turn
- `next-move-analysis.md` — situation analysis answering: what changed, what's known vs inferred, bait, opportunities, risks, affected objectives
- `prompt-candidates.json` — multiple candidate prompts with rationale, risk, and objective alignment

### DM Profile (Framework)

A running profile of inferred DM behavior patterns.

- Updated incrementally as patterns emerge
- Includes tone, structure, hint patterns, adversarial level, and formatting preferences
- All entries include confidence scores

### Semantic Extraction Layer (Optional, LLM-based)

An automated pipeline that uses an LLM to extract structured data from transcript turns.

- **Four-agent pipeline**: Entity Discovery → Entity Detail → Relationship Mapper → Event Extractor
- Prompt templates in `templates/extraction/` define each agent's behavior
- `tools/semantic_extraction.py` orchestrates the pipeline
- `tools/catalog_merger.py` merges agent outputs into `framework/catalogs/`; includes per-pair relationship consolidation, `_dedup_relationships()` safety net (#183), and `cleanup_dangling_relationships()` to remove refs to non-existent entities (#184)
- `tools/llm_client.py` provides a provider-agnostic LLM wrapper (OpenAI, Ollama, Google Gemini, etc.)
- `config/llm.json` configures the LLM provider, model, and endpoint
- All extracted entities are validated against `schemas/entity.schema.json` before merging
- Entities below a confidence threshold are logged but not cataloged
- Batch mode checkpoints progress every 25 turns (configurable via `config/llm.json` `checkpoint_interval`, default 25) for resume after interruption
- **Event type coercion** (#198): Before schema validation, `_coerce_event_fields()` remaps invalid event `type` values to the nearest valid enum value using `_EVENT_TYPE_REMAP`. For example, `"acquisition"` → `"discovery"`, `"conflict"` → `"encounter"`. Unknown types fall back to `"other"`. This prevents valid events from being silently dropped when the LLM produces a type not in the schema enum.
- **Segmented extraction** (`--segment-size N`): For long sessions (300+ turns), extraction runs in segments of N turns, each starting with a fresh catalog. This prevents context window saturation in the entity discovery prompt, which includes the full known-entities table. After all segments complete, a reconciliation pass merges entities by ID and name, stitches event timelines, and joins relationships across segment boundaries. Segment size should be tuned to the model's effective context capacity (recommended: 100-150 for 14B models with 32K context).
- Birth events trigger automatic entity creation for named children, with child IDs added to event `related_entities`
- Stub backfill gathers context from both `related_entities` references and entity name mentions in event descriptions
- **Type-specific orphan thresholds** (#185): The post-batch orphan sweep uses different minimum-reference thresholds by entity type — characters require 3 event references, locations require 2, and factions require 1. This improves late-game entity discovery for location and faction entities that appear less frequently in events.
- **Turn-tag ID normalization** (#185): Entity IDs with LLM-generated turn-tag suffixes (e.g., `char-shaman-turn-082`) are normalized to their canonical form (`char-shaman`) during orphan stub creation, post-batch orphan sweep, and the dedup pre-pass. The `_normalize_entity_id()` function strips `-turn-NNN` suffixes when the canonical form already exists in catalogs or the tagged form is unknown.
- **Name-mention discovery** (#185): After the post-batch orphan sweep, `_name_mention_discovery()` scans all event descriptions for capitalized proper names that do not match any known entity. Names appearing in 2+ distinct events get a character stub. This catches named characters like Borin and Elder Lyra that the LLM failed to include in `related_entities` arrays, closing the gap where entities are mentioned narratively but never formally extracted.
- **Season coercion** (#185): `_normalize_season()` in `extract_structured_data.py` maps LLM season output variants (hyphenated, compound, colloquial) to schema-compliant enum values. The `state.schema.json` season enum includes sub-season variants: `early_spring`, `late_summer`, `mid_winter`, etc.
- **Periodic entity refresh** (#161, #182): Every `entity_refresh_interval` turns (default 50), the pipeline identifies entities whose `last_updated_turn` has fallen behind by more than the interval. Up to `entity_refresh_batch_size` (default 10) of the most stale entities are re-extracted using recent transcript context where they are mentioned. This prevents entities from going permanently stale in long sessions. The refresh uses the same entity-detail LLM template and merges results via `merge_entity()`. **Dynamic scaling** (#182): when the catalog has 60+ entities, the effective batch size scales to `max(batch_size, catalog_size // 5)`, capped at 25. **Type-aware slot allocation** (#182): refresh slots are distributed proportionally by entity type — characters 50%, locations 20%, items 20%, factions 10% — so narratively important characters are refreshed more frequently. Unused type slots overflow to other types. When an events list is available, entities referenced in more events win ties between entities with similar staleness gaps.
- **Entity detail envelope handling** (#168): Entity detail extraction accepts both the standard `{"entity": {...}}` envelope format and flat entity responses where the LLM omits the wrapper. The `_unwrap_entity_response()` helper normalizes both formats, preventing valid entity data from being silently dropped with smaller models.
- **Non-standard key coercion** (#170, #172): `_coerce_entity_fields()` remaps common non-standard top-level keys returned by smaller LLMs into their correct V2 schema locations before validation. Volatile remaps include `equipment`/`inventory`/`equipment_and_tools`/`item_equipment`/`item_inventory` → `volatile_state.equipment`, `location`/`current_location` → `volatile_state.location`, `status`/`health_status`/`status_effects` → `volatile_state.condition`. Stable remaps include `abilities`/`skills_and_abilities` → `stable_attributes.abilities`, `alignment` → `stable_attributes.alignment`, `weaknesses` → `stable_attributes.weaknesses`. Relationship variants (`relations`, `character_relations`, `faction_relations`, `item_relations`, `items_relations`, `current_relationships`) are adopted into `relationships`. Known noise keys (26+, including `events`, `activities`, `activity_history`, ephemeral per-turn data) are discarded. A `_new` suffix strip pass normalizes diff-format keys like `relationships_new` → `relationships` before remap/discard runs. Null values in `stable_attributes.*.value` are stripped before validation (#178).
- **PC extraction handling**: The player character (char-player) is extracted every turn with an extended timeout and a separate `pc_max_tokens` limit (configurable in `config/llm.json`) to accommodate its larger context. If PC extraction fails for 20 consecutive turns (`_PC_SKIP_THRESHOLD`), it enters a cooldown cycle: skipping 50 turns, then retrying for 5 turns, repeating until a success resets the counter. This replaces the previous permanent-skip behavior so the PC can recover from transient failure streaks.
- **PC alias merge** (`_merge_pc_aliases`): Post-extraction pass that identifies character entities which are actually aliases of the player character (e.g., the PC's proper name appearing as a separate entity). Candidates must appear ≥2 times in PC event text within a ≤3 turn span. Three guards prevent false positives: (1) **co-occurrence guard** — skips merge if the candidate and char-player both appear in the same event's `related_entities`, indicating distinct characters; (2) **relationship guard** — skips merge if either entity has a relationship targeting the other; (3) **alias blocklist** (#186) — rejects meta-labels like "player character", "pc", "protagonist" via `_PC_ALIAS_BLOCKLIST`, and strips any existing blocklisted aliases from the PC entity.
- Biography sections use LLM-generated descriptive titles (not generic "Phase" labels), cached in `.synthesis.json` sidecars
- Wiki pages include cross-page entity links: the first mention of each known entity in biography prose, relationship tables, event timelines, and member/connection lists is a clickable markdown link to that entity's wiki page. Link resolution uses relative paths across entity types.
- **Coreference hints** (`apply_coreference_hints`): Optional manual merge rules in `sessions/*/coreference-hints.json`. Each entry maps a canonical entity ID to variant names and ID patterns. After the automatic dedup pass, the pipeline loads any hints file from the session directory and deterministically merges variant entities into their canonical counterpart — absorbing relationships, events, and stable attributes, deleting variant files, and rewriting all dangling references. Validated against `schemas/coreference-hints.schema.json`.

### Timeline Layer (Framework)

Estimated timeline of in-game events, anchored to a configurable reference point.

- `framework/catalogs/timeline.json` — temporal markers with day estimates, seasons, and confidence scores
- `tools/temporal_extraction.py` — pattern-based extraction of season keywords, biological markers, construction milestones, and time-skip language
- `templates/extraction/temporal-signals.md` — optional LLM prompt template for ambiguous temporal estimation
- Calibrated from biological markers (pregnancies), construction timelines, and seasonal descriptions
- Reference anchor defaults to turn-001 (Day 0); a named anchor (e.g., settlement founding) can be set in config
- Feeds into wiki page generation for character ages and event dating (estimated day column in event timelines, season labels in infoboxes)

---

## Schemas

All data structures are defined in `schemas/`. See each schema file for field definitions.

| Schema | Purpose |
|---|---|
| `turn.schema.json` | A single transcript turn |
| `entity.schema.json` | A character, location, faction, or item |
| `plot-thread.schema.json` | A narrative thread with status and open questions |
| `state.schema.json` | Current world and player state |
| `objective.schema.json` | A player objective |
| `evidence.schema.json` | A piece of tagged evidence |
| `prompt-candidate.schema.json` | A candidate next-player-prompt |
| `dm-profile.schema.json` | Inferred DM behavior profile |
| `timeline.schema.json` | A temporal marker (season transition, time skip, biological marker, etc.) |
| `coreference-hints.schema.json` | Manual coreference hints for entity fragmentation resolution |

---

## Tool Scripts

| Script | Purpose |
|---|---|
| `tools/bootstrap_session.py` | Import an existing transcript into a session |
| `tools/ingest_turn.py` | Add a new turn to a session |
| `tools/update_state.py` | Regenerate session-local derived scaffolds, turn summaries, and structured extraction outputs |
| `tools/analyze_next_move.py` | Generate next-move analysis and prompt candidates |
| `tools/validate.py` | Validate all JSON files against schemas |
| `tools/validate_extraction.py` | Post-extraction validation against curated ground truth (alias merges, missing entities, staleness) |
| `tools/semantic_extraction.py` | LLM-based entity/relationship/event extraction pipeline |
| `tools/temporal_extraction.py` | Pattern-based temporal signal extraction and day estimation |
| `tools/catalog_merger.py` | Merge extracted entities into framework catalog files |
| `tools/llm_client.py` | Provider-agnostic LLM client (OpenAI, Ollama, Google Gemini, etc.) |
| `tools/start_extraction_detached.ps1` | Launch semantic extraction in a detached process with log/PID files |
| `tools/watch_extraction_detached.ps1` | Show status and tail logs for detached extraction runs |
| `tools/stop_extraction_detached.ps1` | Stop detached extraction runs by PID file |

---

## Design Decisions

### Why file-based?

File-based storage is human-readable, git-friendly, and easy to edit manually or with Copilot. No database required.

### Why separate raw from derived?

Raw files are the source of truth. If analysis is wrong, derived files can be regenerated without loss.

### Why JSON schemas?

Schemas enable validation, documentation, and Copilot-assisted editing with type safety.

### Why no game-system assumptions?

The system learns the game world from the transcript. It works for any AI DM, any genre, any ruleset.
