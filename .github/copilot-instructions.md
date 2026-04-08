# Copilot Instructions — narrative-state-engine

This repository is a player-side assistant for AI-driven RPG and interactive fiction sessions.
These instructions govern how Copilot assists when editing files in this repository.

---

## Core Rules

### 1. Never Modify Raw Transcript Text

Files under `sessions/*/raw/` and `sessions/*/transcript/` are **immutable sources of truth**.

- Do not edit, paraphrase, reorder, or summarize text in these files.
- Do not correct spelling or grammar in raw transcript files.
- Only append new turns; never modify existing ones.
- All derived content must be generated from raw files, not the other way around.

### 2. Always Preserve Provenance

Every derived fact must reference its source turn.

- Use `source_turns` fields in evidence, state, and objective files.
- When updating catalogs, record `first_seen_turn` and `last_updated_turn`.
- Do not generate facts that cannot be traced to a specific turn ID.

### 3. Separate Fact from Inference

Strictly distinguish between what the DM stated and what is inferred.

- Use `explicit_evidence` only for things the DM directly stated.
- Use `inference` for conclusions derived by the player-assistant.
- Use `dm_bait` for information that appears designed to lure or mislead.
- Use `player_hypothesis` for ideas the player is considering but has not validated.
- Never present an inference as a confirmed fact in summaries or analysis.
- Always include a `confidence` score (0.0–1.0) on inferences.

### 4. Update Catalogs Consistently

When new entities, locations, factions, or items appear in a turn:

- Add them to the appropriate catalog file in `framework/catalogs/`.
- Use the canonical schema from `schemas/entity.schema.json`.
- Do not duplicate existing entries; update `last_updated_turn` instead.
- Keep descriptions concise and factual (no inferred attributes unless tagged as inference).

### 5. Keep Summaries Concise

- `derived/turn-summary.md` should be 3–8 bullet points maximum per turn.
- `framework/story/summary.md` should be a high-level arc summary, not a full retelling.
- Do not pad summaries with flavor text or speculation.

### 6. Generate Multiple Prompt Options

When generating `derived/prompt-candidates.json`:

- Always generate at least 3 candidates with different strategies.
- Each candidate must include: `id`, `recommendation_mode`, `proposed_prompt`, `rationale`, `expected_upside`, `risk`, and `objective_refs`.
- Use `desired_outcome` mode by default; optionally include `roleplay_consistent` and `all_options`.
- Cover at least one safe option and one probing or information-gathering option.

### 7. Do Not Invent Unsupported Facts

- Do not add entities, locations, or plot details that have not appeared in the transcript.
- Do not infer motivations or backstory unless explicitly supported.
- If a gap exists in the narrative, note it as an `open_question` in the plot-thread, not as a fact.

---

## File Conventions

| Path pattern | Purpose | Mutable? |
|---|---|---|
| `sessions/*/raw/` | Original full transcript | No |
| `sessions/*/transcript/turn-*.md` | Individual turn files | No |
| `sessions/*/derived/` | All derived outputs | Yes (regenerate each turn) |
| `framework/catalogs/` | Running entity/location catalogs | Yes (append only) |
| `framework/objectives/objectives.json` | Current player objectives | Yes |
| `framework/dm-profile/dm-profile.json` | Inferred DM behavior profile | Yes |
| `framework/story/summary.md` | High-level story arc | Yes |
| `schemas/*.schema.json` | JSON schemas | No |

---

## JSON Schema Compliance

All JSON files must validate against the corresponding schema in `schemas/`.

Run `python tools/validate.py` to check compliance before committing.

---

## Workflow Reminder

After each DM turn:
1. Append the turn to `sessions/*/raw/full-transcript.md`
2. Create a new `sessions/*/transcript/turn-NNN-dm.md`
3. Run `python tools/update_state.py` or ask Copilot to update derived files
4. Run `python tools/analyze_next_move.py` or ask Copilot to generate analysis
5. Review `derived/next-move-analysis.md` and `derived/prompt-candidates.json`
