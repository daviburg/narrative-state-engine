# Copilot Prompt Template — Ingest Session Turn

Use this prompt in VS Code Copilot Chat or as a GitHub Copilot task to ingest a new DM turn
and update all derived session artifacts.

---

## When to use
After receiving a DM response in your RPG session and adding it to the transcript.

## Required context files to load first
- `sessions/{session_id}/raw/full-transcript.md` (or the new turn file)
- `sessions/{session_id}/derived/state.json`
- `sessions/{session_id}/derived/evidence.json`
- `sessions/{session_id}/derived/objectives.json`
- `framework/catalogs/characters.json`
- `framework/catalogs/locations.json`
- `framework/catalogs/plot-threads.json`
- `framework/dm-profile/dm-profile.json`

---

## Prompt

```
A new DM turn has been added to sessions/{session_id}/transcript/turn-{NNN}-dm.md.

Please update the following derived files based on this new turn only.
Do not modify or re-derive older turns.

1. sessions/{session_id}/derived/turn-summary.md
   - Add 3–8 bullet points summarising what the DM revealed or changed in this turn.
   - Separate: new facts | new entities | changes to existing state | new risks or opportunities.

2. sessions/{session_id}/derived/state.json
   - Update `as_of_turn` to turn-{NNN}.
   - Update `current_world_state`, `player_state`, `opportunities`, `risks`, and `active_threads`
     based on what changed in this turn.
   - Use explicit_evidence only for things the DM directly stated.

3. sessions/{session_id}/derived/evidence.json
   - Add new evidence entries for any new facts, inferences, or possible DM bait.
   - Tag each entry correctly: explicit_evidence / inference / dm_bait / player_hypothesis.
   - Set confidence scores: 1.0 for explicit_evidence, lower for inference and bait.
   - Reference source_turns: ["turn-{NNN}"].

4. framework/catalogs/characters.json
   - Add any new NPCs that appeared for the first time.
   - Update last_updated_turn for existing NPCs who had new information revealed.

5. framework/catalogs/locations.json
   - Add or update locations mentioned in this turn.

6. framework/catalogs/plot-threads.json
   - Update any plot threads that advanced or changed.
   - Add new open_questions raised by this turn.

7. framework/dm-profile/dm-profile.json
   - If this turn revealed new evidence about DM behavior (hint style, tone, OOC channels, etc.),
     update the relevant fields conservatively. Always include confidence values and source_refs.

Constraints:
- Never modify the raw transcript file.
- Never present an inference as a confirmed fact.
- Every derived entry must reference source_turns.
- Keep catalog entries concise and factual.
```

---

## Expected outputs
- Updated `turn-summary.md`
- Updated `state.json`
- Updated `evidence.json`
- Updated catalog files (characters.json, locations.json, plot-threads.json)
- Optionally updated `dm-profile.json`
