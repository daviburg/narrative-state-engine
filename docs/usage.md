# Usage Guide

## Setup

### Prerequisites

- Python 3.9+
- No external Python packages required for core tools

### Creating a New Session

Create a session directory and metadata file:

```bash
mkdir -p sessions/session-001/{transcript,raw,derived}

cat > sessions/session-001/metadata.json << 'EOF'
{
  "session_id": "session-001",
  "title": "Session 1",
  "start_date": "2026-01-01",
  "description": "Opening session",
  "turn_count": 0
}
EOF
```

---

## Ingesting Turns

### Adding a DM Response

```bash
python tools/ingest_turn.py \
  --session sessions/session-001 \
  --speaker dm \
  --text "You arrive at the village of Thornhaven at dusk. The streets are empty, and every door is shut tight. A crow watches you from the inn's sign."
```

### Adding a Player Prompt

```bash
python tools/ingest_turn.py \
  --session sessions/session-001 \
  --speaker player \
  --text "I approach the inn and knock on the door."
```

The script will:
- Assign a turn ID and sequence number
- Create `sessions/session-001/transcript/turn-NNN-{speaker}.md`
- Append to `sessions/session-001/raw/full-transcript.md`

### Bootstrapping an Existing Transcript

If you already have a large transcript file, bootstrap a session in one pass:

Use a local-only import folder (gitignored) for raw source text:

```bash
mkdir -p sessions/_import
# Place your transcript at:
# sessions/_import/session-001-full-transcript.txt
```

```bash
python tools/bootstrap_session.py \\
  --session sessions/session-001 \\
  --file sessions/_import/session-001-full-transcript.txt
```

Useful flags:
- `--dry-run` preview parsed turns and files before writing
- `--format {auto,markdown,labeled,alternating}` override auto-detect
- `--dm-label` / `--player-label` for non-default speaker labels

---

## Updating State

After ingesting new turns, update the derived state:

```bash
python tools/update_state.py --session sessions/session-001
```

This regenerates:
- `sessions/session-001/derived/turn-summary.md`
- `sessions/session-001/derived/state.json`
- `sessions/session-001/derived/objectives.json`
- `sessions/session-001/derived/evidence.json`

Current `update_state.py` behavior is intentionally limited to session-local derived files.

It does **not** currently update:
- `framework/catalogs/*.json`
- `framework/story/*`
- `framework/dm-profile/dm-profile.json`

Those updates are currently manual/Copilot-assisted.

---

## Generating Next-Move Analysis

```bash
python tools/analyze_next_move.py --session sessions/session-001
```

This generates:
- `sessions/session-001/derived/next-move-analysis.md` — situation analysis
- `sessions/session-001/derived/prompt-candidates.json` — candidate prompts

### Prompt Generation Modes

| Mode | Description |
|---|---|
| `desired_outcome` | Prompts optimized for achieving player objectives (default) |
| `roleplay_consistent` | Prompts that stay in character |
| `all_options` | A broad set covering safe, probing, and aggressive approaches |

To request a specific mode:

```bash
python tools/analyze_next_move.py --session sessions/session-001 --mode all_options
```

---

## Validating JSON

```bash
# Validate a specific session
python tools/validate.py --session sessions/session-001

# Validate the framework
python tools/validate.py --framework framework

# Validate everything
python tools/validate.py --all
```

---

## Using with VS Code Copilot

Open the repo in VS Code with GitHub Copilot enabled.

Copilot will follow the instructions in `.github/copilot-instructions.md` automatically.

### Recommended Copilot Prompts

**After adding a DM turn:**
```
Update state.json, objectives.json, and evidence.json based on the latest DM turn in the transcript.
```

**To generate analysis:**
```
Generate next-move-analysis.md and prompt-candidates.json for the current session state.
```

**To explore entities:**
```
What do we know about [character/location] from the transcript? Separate explicit evidence from inference.
```

**To check objectives:**
```
Which objectives are most at risk based on the current state? What actions would advance them?
```

**To probe DM patterns:**
```
Based on the DM profile, what hints might be bait vs. genuine? Update dm_bait evidence accordingly.
```

---

## File Editing Guidelines

### Raw Transcript Files

**Never edit.** Only append new turns using `ingest_turn.py`.

### Derived Files

Safe to edit manually or via Copilot. `update_state.py` regenerates `turn-summary.md` and ensures scaffold files exist.

### Catalog Files

Append only. Do not delete or rename existing entries — use `last_updated_turn` to track changes.

### Schema Files

Do not edit unless the data model needs to change. If schemas change, re-validate all data files.

---

## Example Session

See `examples/demo-session/` for a complete worked example with 6 turns, 3 entities, 2 plot threads, and annotated analysis.
