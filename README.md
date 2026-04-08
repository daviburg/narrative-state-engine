# narrative-state-engine

A player-side assistant framework for AI-driven RPG and interactive fiction sessions.

This system captures DM responses and player prompts, preserves full transcripts, extracts structured narrative state, maintains catalogs and storyline continuity, tracks player objectives, analyzes strategy, and generates suggested next player prompts.

**This is NOT a DM engine.** It supports the player interacting with an external AI DM.

---

## Purpose

Given a sequence of DM outputs and player prompts, the system:

1. Preserves the exact transcript (immutable source)
2. Maintains a structured understanding of the narrative state
3. Tracks player objectives (short-term and long-term)
4. Identifies evidence, inference, and possible DM bait
5. Infers DM behavior patterns over time
6. Analyzes the current situation
7. Suggests multiple candidate player prompts

---

## Repository Structure

```
README.md
LICENSE
.github/copilot-instructions.md

docs/
  architecture.md
  usage.md

schemas/
  turn.schema.json
  entity.schema.json
  plot-thread.schema.json
  state.schema.json
  objective.schema.json
  evidence.schema.json
  prompt-candidate.schema.json
  dm-profile.schema.json

framework/
  catalogs/
    characters.json
    locations.json
    factions.json
    items.json
    plot-threads.json
  objectives/
    objectives.json
  dm-profile/
    dm-profile.json
  story/
    summary.md
    world-state.md
  strategy/
    heuristics.md

sessions/
  session-001/
    metadata.json
    transcript/
      turn-001-player.md
      turn-002-dm.md
    raw/
      full-transcript.md
    derived/
      turn-summary.md
      state.json
      objectives.json
      evidence.json
      next-move-analysis.md
      prompt-candidates.json

tools/
  bootstrap_session.py
  ingest_turn.py
  update_state.py
  analyze_next_move.py
  validate.py

examples/
  demo-session/
```

---

## Quick Start

### Prerequisites

- Python 3.9+
- No external dependencies required for core tools

### Ingesting a Turn

After each exchange with your AI DM, add the turn to a session:

```bash
# Add a DM response (turn 003)
python tools/ingest_turn.py \
  --session sessions/session-001 \
  --speaker dm \
  --text "The innkeeper leans forward and whispers: 'The old tower has been sealed for twenty years. Those who enter do not return.'"

# Add a player prompt
python tools/ingest_turn.py \
  --session sessions/session-001 \
  --speaker player \
  --text "I ask the innkeeper if anyone has tried to investigate the tower recently."
```


### Bootstrapping From an Existing Transcript

If you already have a large transcript, import it in one step:

Put the source text in a local-only folder that is gitignored:

```bash
mkdir -p sessions/_import
# Place your raw transcript text at:
# sessions/_import/session-001-full-transcript.txt
```

```bash
python tools/bootstrap_session.py \
  --session sessions/session-001 \
  --file sessions/_import/session-001-full-transcript.txt
```

Use `--dry-run` first to preview parsed turns and writes.

### Updating State

After ingesting new turns, refresh the derived scaffold and summary:

```bash
python tools/update_state.py --session sessions/session-001
```

Current automated behavior:
- Rebuilds `sessions/session-001/derived/turn-summary.md` from transcript files
- Creates scaffold files if missing: `state.json`, `objectives.json`, `evidence.json`
- Updates only `state.json.as_of_turn`

Not automated yet:
- Framework catalog updates (`framework/catalogs/*.json`)
- Framework story updates (`framework/story/*`)
- Framework DM profile updates (`framework/dm-profile/dm-profile.json`)

Those updates are currently manual/Copilot-assisted.

### Generating Next-Move Analysis

Analyze the current situation and generate prompt candidates:

```bash
python tools/analyze_next_move.py --session sessions/session-001
```

### Validating JSON Files

Check that all JSON files conform to their schemas:

```bash
python tools/validate.py --session sessions/session-001
python tools/validate.py --framework framework
```

---

## Using with VS Code Copilot

This repository is configured for GitHub Copilot via `.github/copilot-instructions.md`.

**Recommended workflow:**

1. Open the session folder in VS Code.
2. Add turns with `tools/ingest_turn.py` (or use `tools/bootstrap_session.py` for existing transcripts).
3. Run `tools/update_state.py` to regenerate `turn-summary.md` and ensure derived scaffolds exist.
4. Ask Copilot to update `derived/state.json`, `derived/objectives.json`, and `derived/evidence.json`.
5. Run `tools/analyze_next_move.py` and refine prompt candidates as needed.
5. Copilot will follow the instructions in `.github/copilot-instructions.md` to ensure consistency.

**Example Copilot prompts:**

- "Update `derived/state.json`, `derived/objectives.json`, and `derived/evidence.json` based on the latest DM turn."
- "Generate 3 prompt candidates focused on the current main objective."
- "What evidence do we have for the tower being cursed vs. just abandoned?"

---

## Design Principles

1. **Raw is immutable** — original transcript text is never modified
2. **Derived is reproducible** — all summaries and state are derived from raw data
3. **Turn-based structure** — session is a sequence of turns; each turn may produce derived updates
4. **Catalog-first context** — agents load summaries and catalogs instead of full transcripts
5. **No assumed game system** — the system learns from the transcript
6. **Player-assistant focus** — helps interpret hints, detect traps, plan strategy, generate prompts
7. **Keep v1 simple** — no unnecessary abstractions

---

## Evidence Classification

All analysis distinguishes:

| Classification | Meaning |
|---|---|
| `explicit_evidence` | Directly stated by the DM |
| `inference` | Derived conclusion — may be wrong |
| `dm_bait` | Possible trap or narrative lure |
| `player_hypothesis` | Tentative idea not yet supported |

**Never present inference as fact.**

---

## Objective Types

| Type | Description |
|---|---|
| `strategic_long_term` | High-level goals spanning multiple sessions |
| `tactical_short_term` | Immediate goals for the current situation |

---

## See Also

- [`docs/architecture.md`](docs/architecture.md) — system design and data flow
- [`docs/usage.md`](docs/usage.md) — detailed usage guide
- [`examples/demo-session/`](examples/demo-session/) — a worked example session
