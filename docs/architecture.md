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
      +---> sessions/*/derived/state.json        +---> framework/catalogs/characters.json
      +---> sessions/*/derived/objectives.json   +---> framework/catalogs/locations.json
      +---> sessions/*/derived/evidence.json     +---> framework/catalogs/factions.json
      +---> sessions/*/derived/turn-summary.md   +---> framework/catalogs/items.json
      |                                          +---> framework/catalogs/events.json
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
- `tools/catalog_merger.py` merges agent outputs into `framework/catalogs/`
- `tools/llm_client.py` provides a provider-agnostic LLM wrapper (OpenAI, Ollama, etc.)
- `config/llm.json` configures the LLM provider, model, and endpoint
- All extracted entities are validated against `schemas/entity.schema.json` before merging
- Entities below a confidence threshold are logged but not cataloged
- Batch mode checkpoints progress every 50 turns for resume after interruption
- Birth events trigger automatic entity creation for named children, with child IDs added to event `related_entities`
- Stub backfill gathers context from both `related_entities` references and entity name mentions in event descriptions
- Biography sections use LLM-generated descriptive titles (not generic "Phase" labels), cached in `.synthesis.json` sidecars
- Wiki pages include cross-page entity links: the first mention of each known entity in biography prose, relationship tables, event timelines, and member/connection lists is a clickable markdown link to that entity's wiki page. Link resolution uses relative paths across entity types.

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

---

## Tool Scripts

| Script | Purpose |
|---|---|
| `tools/bootstrap_session.py` | Import an existing transcript into a session |
| `tools/ingest_turn.py` | Add a new turn to a session |
| `tools/update_state.py` | Regenerate session-local derived scaffolds, turn summaries, and structured extraction outputs |
| `tools/analyze_next_move.py` | Generate next-move analysis and prompt candidates |
| `tools/validate.py` | Validate all JSON files against schemas |
| `tools/semantic_extraction.py` | LLM-based entity/relationship/event extraction pipeline |
| `tools/temporal_extraction.py` | Pattern-based temporal signal extraction and day estimation |
| `tools/catalog_merger.py` | Merge extracted entities into framework catalog files |
| `tools/llm_client.py` | Provider-agnostic LLM client (OpenAI, Ollama, etc.) |

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
