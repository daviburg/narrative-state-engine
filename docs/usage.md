# Usage Guide

## Setup

### Prerequisites

- Python 3.10+
- No external Python packages required for core tools
- **Optional** — for LLM-based semantic extraction:
  - `pip install -r requirements-llm.txt`
  - An OpenAI-compatible LLM endpoint (OpenAI API, Ollama, etc.)
  - Configure `config/llm.json` with provider, model, and endpoint

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

## LLM Model Requirements

Semantic extraction uses an LLM to identify entities, relationships, and events from transcript text. The quality of extraction depends significantly on model size.

### Minimum Requirements

- **Minimum**: 7B parameters — basic entity extraction works but expect some ID format errors and weak coreference resolution
- **Recommended**: 14B+ parameters — reliable JSON output, good coreference, accurate entity classification
- **Best**: GPT-4o or equivalent cloud model — highest accuracy but requires API key and costs per token

### Tested Models

| Model | Parameters | Quantization | VRAM | Extraction Quality |
|---|---|---|---|---|
| `qwen2.5:3b` | 3B | Q4_K_M | ~2.5 GB | Poor — 0% item recall, frequent hallucinations, ID format violations |
| `qwen2.5:14b` | 14B | Q4_K_M | ~9 GB | Good — reliable entity classification, items extracted, acceptable coreference |
| `gpt-4o` (OpenAI) | Unknown | N/A | Cloud | Best — accurate structured JSON, strong coreference, minimal hallucination |

### VRAM Quick Reference

| GPU VRAM | Maximum Model Size (Q4 quantization) |
|---|---|
| 8 GB | Up to 7B |
| 12 GB | Up to 14B |
| 16 GB | Up to 22B |
| 24 GB | Up to 32B |

### Known Limitations of Small Models (<7B)

- Entity IDs generated with wrong type prefixes (e.g., `loc-` for an item)
- Poor coreference resolution — same entity gets multiple catalog entries
- Items rarely or never extracted
- Location/character/faction type confusion
- Hallucinated entities not present in the source text

### Using a Local Model

Configure Ollama or any OpenAI-compatible server in `config/llm.json`:

```json
{
  "provider": "ollama",
  "base_url": "http://localhost:11434/v1",
  "model": "qwen2.5:14b",
  "api_key_env": "",
  "temperature": 0.0,
  "max_tokens": 4096,
  "pc_max_tokens": 8192,
  "timeout_seconds": 180,
  "retry_attempts": 3,
  "batch_delay_ms": 500
}
```

| Field | Description |
|---|---|
| `max_tokens` | Default max output tokens for all LLM extraction calls. |
| `pc_max_tokens` | Max output tokens for **PC entity extraction** only. Defaults to `max_tokens` if omitted. The player-character entity accumulates context over many turns and may need a higher token limit to avoid truncation. |
| `entity_refresh_interval` | Every N turns, find and re-extract stale entities whose `last_updated_turn` has fallen behind by more than N turns. Default: `50`. Set to `0` to disable. |
| `entity_refresh_batch_size` | Maximum number of stale entities to refresh per interval. Default: `5`. Entities are prioritized by staleness (most stale first) and must appear in the transcript since their last update. |

**PC extraction skip-after-failures:** If PC extraction fails for 20 consecutive turns, it is automatically skipped for the remainder of the run to avoid wasting time on doomed LLM calls. An end-of-run summary reports how many turns were skipped. The threshold (20) is defined as `_PC_SKIP_THRESHOLD` in `tools/semantic_extraction.py`.

Or use CLI overrides for one-off runs:

```bash
python tools/bootstrap_session.py \
    --session sessions/my-session \
    --file transcript.txt \
    --framework framework-local \
    --model qwen2.5:14b \
    --base-url http://localhost:11434/v1
```

**Note:** `--model` and `--base-url` override only those settings. The current implementation still reads `api_key_env` from `config/llm.json` (by default this is often `OPENAI_API_KEY`). If your local OpenAI-compatible server does not require an API key, either set the expected environment variable anyway or set `"api_key_env": ""` in `config/llm.json` before running the command.

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

To also run LLM-based semantic extraction on the new turn:

```bash
python tools/ingest_turn.py \
  --session sessions/session-001 \
  --speaker dm \
  --text "..." \
  --extract
```

### Bootstrapping an Existing Transcript

If you already have a large transcript file, bootstrap a session in one pass:

Use a local-only import folder (gitignored) for raw source text:

```bash
mkdir -p sessions/_import
# Place your transcript at:
# sessions/_import/session-001-full-transcript.txt
```

```bash
python tools/bootstrap_session.py \
  --session sessions/session-001 \
  --file sessions/_import/session-001-full-transcript.txt
```

Useful flags:
- `--dry-run` preview parsed turns and files before writing
- `--format {auto,markdown,labeled,alternating}` override auto-detect
- `--dm-label` / `--player-label` for non-default speaker labels

**Important:** The `--player-label` must match the speaker label text used in your source transcript (case-insensitive; pass the label text without the trailing colon). For example, if the transcript uses `Fenouille Moonwind:` as the player label, pass `--player-label "Fenouille Moonwind"`. If the label text doesn't match, the parser won't recognize player turns and their text will be appended to the preceding DM turn, contaminating extraction input.

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
- `framework/story/*`
- `framework/dm-profile/dm-profile.json`

Those updates are currently manual/Copilot-assisted.

For automated catalog updates, see Semantic Extraction below.

---

## Semantic Extraction (Optional)

The semantic extraction pipeline uses an LLM to automatically extract entities, relationships, and events from transcript turns and merge them into `framework/catalogs/`.

### Setup

1. Install the optional LLM dependency:
   ```bash
   pip install -r requirements-llm.txt
   ```

2. Configure `config/llm.json` (defaults to local Ollama):
   ```json
   {
     "provider": "openai",
     "base_url": "http://localhost:11434/v1",
     "model": "qwen2.5:14b",
     "api_key_env": ""
   }
   ```

   For a cloud provider (e.g. OpenAI):
   ```json
   {
     "provider": "openai",
     "base_url": "https://api.openai.com/v1",
     "model": "gpt-4o",
     "api_key_env": "OPENAI_API_KEY"
   }
   ```

### Batch Mode (Bootstrap)

When bootstrapping a session, semantic extraction runs automatically over all turns if the LLM is configured:

```bash
python tools/bootstrap_session.py \
  --session sessions/session-001 \
  --file sessions/_import/session-001-full-transcript.txt
```

The pipeline processes each turn through four agents:
1. **Entity Discovery** — identify entities mentioned in the turn
2. **Entity Detail Extractor** — extract/update attributes per entity
3. **Relationship Mapper** — identify cross-entity relationships
4. **Event Extractor** — identify narrative events

Progress is checkpointed every 50 turns and can resume after interruption.

### Segmented Extraction

For sessions with 200+ turns on models with ≤32K context windows, use
segmented extraction to prevent quality degradation in late turns:

```bash
python tools/bootstrap_session.py \
  --session sessions/session-001 \
  --file sessions/_import/session-001-full-transcript.txt \
  --segment-size 100
```

Each segment processes turns with a fresh entity catalog. After all segments
complete, entities are automatically reconciled across segment boundaries by
ID and name matching.

Recommended segment sizes:
- 7B models (8K context): 50 turns
- 14B models (32K context): 100-150 turns
- 70B+ models (128K context): 300+ turns (may not need segmentation)

Segment size 0 (the default) preserves legacy single-pass behavior.

### Coreference Hints

When the automatic dedup pass doesn't catch all duplicates (e.g., a character
referred to by descriptions like "broad figure" before being named), you can
provide a manual hints file to force deterministic merging.

Create `sessions/<session>/coreference-hints.json`:

```json
{
  "character_groups": [
    {
      "canonical_name": "Kael",
      "canonical_id": "char-kael",
      "variant_names": ["broad figure", "young hunter", "brave warrior"],
      "variant_id_patterns": ["char-broad-figure", "char-young-hunter", "char-brave-warrior"],
      "notes": "Kael appears unnamed before turn-149"
    }
  ]
}
```

Fields:
- `canonical_name` / `canonical_id` — the entity that survives the merge
- `variant_names` — entity names to match (case-insensitive)
- `variant_id_patterns` — entity IDs to match exactly
- `notes` — optional documentation

The hints file is validated against `schemas/coreference-hints.schema.json`.
If no hints file exists, the pipeline skips this step gracefully.

During batch extraction, coreference hints run after the automatic dedup pass.
Variant entities are merged into the canonical entity: relationships, events,
and stable attributes are absorbed, variant files are deleted from disk, and
all dangling references are rewritten.

### Incremental Mode (Ingest)

Pass `--extract` to `ingest_turn.py` to run semantic extraction on a single new turn:

```bash
python tools/ingest_turn.py \
  --session sessions/session-001 \
  --speaker dm \
  --text "The elder reveals that the crystal was shattered decades ago." \
  --extract
```

See [`docs/semantic-extraction-design.md`](semantic-extraction-design.md) for full pipeline design.

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

## Timeline Configuration

The pipeline tracks in-game time progression by extracting temporal signals (season keywords, biological markers, construction milestones, time-skip language) from transcript turns. By default, turn-001 is Day 0.

Timeline data is stored in `framework/catalogs/timeline.json` and conforms to `schemas/timeline.schema.json`.

### Reference Anchor

The reference anchor defaults to turn-001 = Day 0. To use a custom anchor, pass a `timeline_anchor` dict when calling `synthesize_entity()` or `assemble_character_page()`:

```python
anchor = {
    "turn": "turn-292",
    "label": "Foundation of the Quiet Weave",
    "day": 0
}
```

A future release will support loading the anchor from `config/llm.json` automatically.

Events before the anchor receive negative day estimates; events after receive positive.

### Season Granularity

The timeline uses 12 fine-grained season labels: `early_winter`, `mid_winter`, `late_winter`, `early_spring`, `mid_spring`, `late_spring`, `early_summer`, `mid_summer`, `late_summer`, `early_autumn`, `mid_autumn`, `late_autumn`.

### Day Estimation

Day offsets are estimated using a configurable days-per-turn ratio (default: 3.5). Confidence scores decrease with distance from the anchor. For campaigns with variable pacing, day estimates serve as rough approximations.

### Wiki Integration

When timeline data is available, wiki pages include:
- **Infobox**: "First Seen Day" with estimated day and season label
- **Event Timeline**: An "Est. Day" column showing approximate in-game day for each event

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

## Post-Extraction Validation

After running semantic extraction, validate the output against curated ground truth to catch entity-level problems that schema validation alone cannot detect (false alias merges, missing characters, coreference fragmentation, entity staleness).

### Running

```bash
# Default: validates framework-local/catalogs against full-session ground truth
python tools/validate_extraction.py --catalog-dir framework-local/catalogs

# Custom ground truth fixture
python tools/validate_extraction.py --catalog-dir framework-local/catalogs \
    --ground-truth tests/fixtures/extraction-ground-truth-full-session.json
```

### Interpreting the Scorecard

The script checks 7 categories and prints a scorecard with PASS/WARN/FAIL for each item:

| Category | What it checks |
|---|---|
| Independent Characters | Expected NPCs exist as separate catalog entities |
| PC Aliases | Only legitimate aliases appear on char-player |
| Must-Not-Merge | Named entities were not incorrectly absorbed into other entities |
| Coreference Groups | Pre-naming descriptions merged into the canonical entity |
| Staleness | Entity `last_updated_turn` is within expected range |
| Locations (late-game) | Expected late-game locations exist in catalogs |
| Factions (late-game) | Expected late-game factions exist in catalogs |

Exit code 0 means no FAILs; exit code 1 means at least one FAIL was found.

### Ground Truth Fixtures

Ground truth files live in `tests/fixtures/` and define the expected extraction output for a session or turn range. The full-session fixture (`extraction-ground-truth-full-session.json`) covers turns 1–345 of `session-import` and is derived from `docs/design-synthesis-layer.md`.

---

## Example Session

See `examples/demo-session/` for a complete worked example with 6 turns, 3 entities, 2 plot threads, and annotated analysis.
