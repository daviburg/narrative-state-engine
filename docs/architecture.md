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
      v
tools/update_state.py
      |
      +---> framework/catalogs/           (characters, locations, factions, items, plot-threads)
      +---> framework/objectives/         (objectives.json)
      +---> framework/dm-profile/         (dm-profile.json)
      +---> framework/story/              (summary.md, world-state.md)
      +---> sessions/*/derived/state.json
      +---> sessions/*/derived/objectives.json
      +---> sessions/*/derived/evidence.json
      |
      v
tools/analyze_next_move.py
      |
      +---> sessions/*/derived/next-move-analysis.md
      +---> sessions/*/derived/prompt-candidates.json
```

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

---

## Tool Scripts

| Script | Purpose |
|---|---|
| `tools/ingest_turn.py` | Add a new turn to a session |
| `tools/update_state.py` | Update catalogs, objectives, evidence, and summaries |
| `tools/analyze_next_move.py` | Generate next-move analysis and prompt candidates |
| `tools/validate.py` | Validate all JSON files against schemas |

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
