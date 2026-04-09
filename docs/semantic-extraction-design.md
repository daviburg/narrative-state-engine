# Semantic Extraction Architecture — Design Document

**Issue:** #43 — Auto-populate entity catalogs during bootstrap import
**Phase:** 1 (Design)
**Status:** Proposed

---

## 1. Problem Statement

After importing a 344-turn session transcript, all six entity catalog files remain empty arrays. The current tooling (`bootstrap_session.py`, `extract_structured_data.py`) handles structural parsing and regex-based marker extraction but has no ability to comprehend natural language. Entity extraction — identifying characters, locations, factions, items, and their relationships from narrative prose — requires language-level understanding that does not exist in the codebase.

The `copilot-instructions.md` rules (provenance, fact-vs-inference, catalog updates) were written for an LLM agent, but no mechanism exists to invoke one as part of the extraction pipeline. This document defines the architecture to bridge that gap.

---

## 2. Design Principles

1. **Scripts orchestrate, agents comprehend.** Python scripts handle file I/O, sequencing, validation, and merging. LLM agents handle language understanding and return structured JSON.
2. **Narrow context per agent call.** Each invocation receives only the data it needs — one turn plus relevant existing state — not the full transcript.
3. **Schema-validated outputs.** Every agent response is validated against the corresponding JSON schema before being written to disk. Invalid responses are rejected and logged.
4. **Provenance is mandatory.** Every extracted fact must reference its `source_turn`. Agents are instructed to include provenance; the script layer enforces it.
5. **Fact vs. inference separation.** Agent prompts explicitly require this distinction. The script layer verifies that `confidence` scores are present on inferred attributes.
6. **Provider-agnostic.** The LLM integration layer abstracts the provider. The same extraction logic works with OpenAI, Azure OpenAI, local Ollama, or any OpenAI-compatible endpoint.
7. **Idempotent batch processing.** Running extraction over the same turns twice produces the same catalogs (modulo non-determinism in LLM output, which is mitigated by temperature 0 and validation).

---

## 3. LLM Integration Approach

### 3.1 Decision: OpenAI-compatible API with pluggable provider

The integration layer uses the OpenAI Chat Completions API format (`/v1/chat/completions`). This is the de facto standard supported by:

- **OpenAI** (GPT-4o, GPT-4.1)
- **Azure OpenAI Service** (same models, enterprise data residency)
- **Ollama** (local models via `http://localhost:11434/v1/`)
- **LM Studio**, **vLLM**, **llama.cpp server** (local alternatives)
- **Anthropic** (via adapter or native SDK as alternate provider)

### 3.2 Configuration

A new configuration file `config/llm.json` controls provider settings:

```json
{
  "provider": "openai",
  "base_url": "https://api.openai.com/v1",
  "model": "gpt-4o",
  "api_key_env": "OPENAI_API_KEY",
  "temperature": 0.0,
  "max_tokens": 4096,
  "timeout_seconds": 60,
  "retry_attempts": 3,
  "batch_delay_ms": 200
}
```

For local Ollama:

```json
{
  "provider": "ollama",
  "base_url": "http://localhost:11434/v1",
  "model": "llama3.1:8b",
  "api_key_env": null,
  "temperature": 0.0,
  "max_tokens": 4096,
  "timeout_seconds": 120,
  "retry_attempts": 2,
  "batch_delay_ms": 0
}
```

The API key is read from the environment variable named in `api_key_env`, never stored in the config file. When `api_key_env` is `null` (local providers), no authentication is sent.

### 3.3 Python dependency

A single new dependency: `openai>=1.0.0` (the official Python SDK, which supports custom `base_url` for all compatible providers). This is added to `requirements.txt`.

No NLP libraries, no tokenizers, no local ML frameworks. The LLM does the comprehension; the SDK sends HTTP requests.

### 3.4 LLM client abstraction

A new module `tools/llm_client.py` provides:

```python
class LLMClient:
    """Thin wrapper around the OpenAI Chat Completions API."""

    def __init__(self, config_path: str = "config/llm.json"):
        ...

    def extract_json(self, system_prompt: str, user_prompt: str,
                     schema: dict | None = None) -> dict | list:
        """Send a chat completion request and parse the JSON response.

        Args:
            system_prompt: Role instructions for the model.
            user_prompt: The turn text and context.
            schema: Optional JSON schema for response_format (structured outputs).

        Returns:
            Parsed JSON object/array.

        Raises:
            LLMExtractionError: If the response is not valid JSON or
                fails schema validation.
        """
        ...
```

The `extract_json` method uses the OpenAI SDK's `response_format` parameter (JSON mode or structured outputs) when the provider supports it, and falls back to parsing JSON from the response text otherwise. Schema validation via `jsonschema` is always applied regardless of provider capability.

---

## 4. Agent Specialization

### 4.1 Decision: Two-phase extraction with four agent roles

Rather than one monolithic "understand everything" call per turn, extraction is split into a **discovery phase** and an **update phase**, using four specialized agent roles.

### 4.2 Agent roles

#### Agent 1: Entity Discovery

**Purpose:** Identify all entities mentioned in a single turn.

| Attribute | Value |
|---|---|
| **Input** | One turn's text + list of already-known entity IDs/names |
| **Output** | Array of `{ name, type, is_new, existing_id?, description, confidence }` |
| **Scope** | One turn |
| **Why separate** | Discovery is the broadest task — it must read the full turn and recognize any named or described entity. Keeping it separate means the prompt can focus entirely on recognition without being burdened by relationship mapping or attribute updates. |

The discovery agent classifies each mention as one of the entity types in `entity.schema.json`: `character`, `location`, `faction`, `item`, `creature`, `concept`. It also performs **coreference resolution** — matching "the elder" to an existing `char-shaman` entry when the catalog already contains that entity.

For **new** entities, it proposes an `id` following the prefix convention (`char-`, `loc-`, `faction-`, `item-`, `creature-`, `concept-`). For **existing** entities, it returns the `existing_id` from the known-entity list.

#### Agent 2: Entity Detail Extractor

**Purpose:** Extract or update attributes and description for a specific entity based on one turn.

| Attribute | Value |
|---|---|
| **Input** | One turn's text + the entity's current catalog entry (or empty for new) |
| **Output** | Updated entity object conforming to `entity.schema.json` |
| **Scope** | One entity × one turn |
| **Why separate** | Each entity gets focused attention. The agent prompt includes the current state of that entity, so it can determine what's new vs. already known. This prevents the "extract everything at once" problem that produces shallow results. |

This agent runs once per entity discovered in the turn (both new and existing entities that are mentioned). It returns a complete entity object with updated `description`, `attributes`, and `last_updated_turn`.

#### Agent 3: Relationship Mapper

**Purpose:** Identify relationships between entities mentioned in a turn.

| Attribute | Value |
|---|---|
| **Input** | One turn's text + list of entities mentioned in this turn (with their current catalog entries) |
| **Output** | Array of relationship objects: `{ source_id, target_id, relationship, type, direction, confidence }` |
| **Scope** | One turn, cross-entity |
| **Why separate** | Relationships are cross-cutting — they span pairs of entities. Running this after entity discovery ensures all entities are identified before relationships are mapped. |

Relationships conform to the inline `relationships[]` format in `entity.schema.json`.

#### Agent 4: Event Extractor

**Purpose:** Identify narrative events (births, deaths, arrivals, constructions, decisions, discoveries) in a turn.

| Attribute | Value |
|---|---|
| **Input** | One turn's text + current event catalog |
| **Output** | Array of event objects conforming to `event.schema.json` |
| **Scope** | One turn |
| **Why separate** | Events are distinct from entity attributes. A birth is not an attribute of a character — it's a state transition that creates new entities and modifies existing ones. |

### 4.3 Agents NOT included in initial design

The following are deferred to future iterations:

- **Story timeline agent** — Narrative arc summarization. The current `update_state.py` template approach is adequate for now.
- **Mechanics tracker** — Already handled well by regex in `extract_structured_data.py`.
- **DM profile updater** — Requires meta-analysis across turns, not per-turn extraction.
- **Anomaly detector** — Can be derived from entity attributes and events; a dedicated agent adds complexity without clear value in Phase 2.

### 4.4 Invocation sequence per turn

```
Turn text
    │
    ▼
┌─────────────────────────┐
│  1. Entity Discovery     │  → list of (entity_id, name, type, is_new)
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│  2. Entity Detail        │  → updated entity objects (one call per entity)
│     Extractor            │     ⚠ parallelizable across entities
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│  3. Relationship Mapper  │  → relationship updates
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│  4. Event Extractor      │  → event objects
└────────────┬────────────┘
             │
             ▼
     Schema validation
             │
             ▼
     Catalog merge + write
```

Steps 2–4 all receive the output of step 1 as context. Step 2 can process multiple entities in parallel (separate API calls). Steps 3 and 4 are independent of each other and can also run in parallel.

### 4.5 Call volume estimate (batch mode)

For a 344-turn session with ~13 major characters, ~5 locations, and ~3 factions:

| Agent | Calls per turn (avg) | Total calls (344 turns) |
|---|---|---|
| Entity Discovery | 1 | 344 |
| Entity Detail Extractor | 2–5 (entities mentioned in turn) | ~1,000 |
| Relationship Mapper | 1 | 344 |
| Event Extractor | 1 | 344 |
| **Total** | | **~2,000** |

At ~$0.002/call with GPT-4o-mini, full batch extraction costs approximately $4. With a local model (Ollama), cost is zero but runtime is longer.

The `batch_delay_ms` configuration prevents rate-limiting. For OpenAI, 200ms delay keeps well within tier-1 rate limits.

---

## 5. Batch vs. Incremental Strategy

### 5.1 Decision: Unified pipeline, mode selected by caller

The same extraction functions are used in both modes. The difference is only in the loop:

**Bootstrap (batch):**
```python
catalogs = load_empty_catalogs()
for turn in all_turns:
    catalogs = extract_and_merge(turn, catalogs, llm_client)
save_catalogs(catalogs)
```

**Ingest (incremental):**
```python
catalogs = load_existing_catalogs()
catalogs = extract_and_merge(new_turn, catalogs, llm_client)
save_catalogs(catalogs)
```

The `extract_and_merge` function is identical in both cases. It:

1. Calls Entity Discovery with the turn text and current catalog entity names
2. For each discovered entity, calls Entity Detail Extractor
3. Calls Relationship Mapper with all entities mentioned in the turn
4. Calls Event Extractor
5. Validates all outputs against schemas
6. Merges results into the in-memory catalog state

### 5.2 Integration points

**Bootstrap path** — `tools/bootstrap_session.py`, after the `extract_all()` call at the end of `main()`. A new function `extract_semantic_data()` is called with the full turn list and session directory. This function iterates turns and calls `extract_and_merge` for each.

```python
# In bootstrap_session.py main(), after extract_all():
try:
    from semantic_extraction import extract_semantic_batch
    extract_semantic_batch(turn_dicts, session_dir, framework_dir="framework")
except ImportError:
    print("WARNING: Semantic extraction not available (openai not installed).")
```

**Incremental path** — `tools/ingest_turn.py`, after writing the turn file. A new `--extract` flag (default off) triggers semantic extraction for the new turn.

```python
# In ingest_turn.py main(), after write_turn_file():
if args.extract:
    from semantic_extraction import extract_semantic_single
    extract_semantic_single(turn_id, speaker, text, session_dir, framework_dir="framework")
```

### 5.3 Graceful degradation

If the `openai` package is not installed or no LLM provider is configured, semantic extraction is silently skipped with a warning. The existing regex-based extraction continues to work unchanged. This preserves backward compatibility — users who don't want LLM integration lose nothing.

### 5.4 Batch optimization: windowed context

In batch mode, processing 344 turns sequentially means the Entity Discovery agent sees a growing known-entity list. By turn 300, the list might contain 50+ entities. To keep prompts efficient:

- The known-entity list sent to Discovery is formatted as a compact table: `id | name | type` (one line per entity)
- Entity Detail Extractor receives only the single entity's current state, not the full catalog
- If a turn mentions no new entities and no significant changes, the Detail Extractor calls are skipped (Discovery signals this with `is_new: false` and `significant_update: false`)

---

## 6. Privacy Model

### 6.1 What data leaves the machine

When using an external LLM API (OpenAI, Azure OpenAI), the following data is sent per API call:

| Data sent | Sensitivity | Mitigation |
|---|---|---|
| One turn of transcript text (~100–500 words) | **Private** — creative fiction content | Provider data policy applies |
| System prompt (agent instructions) | **Non-sensitive** — generic extraction instructions | N/A |
| Existing catalog entry for one entity | **Low** — structured metadata already derived | N/A |
| Known-entity ID/name list | **Low** — names only, no narrative context | N/A |

The full transcript is **never** sent in a single request. Each call receives at most one turn (~500 words) plus minimal context.

### 6.2 Provider-specific considerations

| Provider | Data leaves machine? | Data retention policy |
|---|---|---|
| **Ollama (local)** | No | N/A — all processing on localhost |
| **Azure OpenAI** | Yes, to Azure region | No training on customer data; configurable data residency |
| **OpenAI API** | Yes, to OpenAI | Not used for training (API TOS as of 2024); 30-day log retention |
| **vLLM / llama.cpp (local)** | No | N/A |

### 6.3 Recommendation

For users with privacy-sensitive content:

1. **Default recommendation:** Use Ollama with a capable local model (Llama 3.1 8B or Mistral 7B). Quality will be lower than GPT-4o but adequate for entity discovery.
2. **Quality recommendation:** Use Azure OpenAI with a regional deployment matching data residency requirements.
3. **Maximum quality:** Use OpenAI GPT-4o or GPT-4.1 with awareness that turn text transits to OpenAI.

The `config/llm.json` file makes this an explicit user choice. No data is sent anywhere without configuration.

---

## 7. Prompt Design

### 7.1 Structure

Each agent role has a dedicated system prompt template stored in `templates/extraction/`. Prompts follow this structure:

```
SYSTEM:
  You are a [role] for an RPG session analysis tool.
  [Role-specific instructions]
  [Output format specification — JSON schema excerpt]
  [Rules: provenance required, fact vs inference, confidence scores]

USER:
  ## Current Turn
  Turn ID: {turn_id}
  Speaker: {speaker}
  Text:
  {turn_text}

  ## Known Entities (if applicable)
  {entity_table}

  ## Current Entity State (for detail extractor only)
  {entity_json}
```

### 7.2 Prototype: Entity Discovery on turn-019-dm

**System prompt (abbreviated):**

```
You are an entity discovery agent for an RPG session transcript analysis tool.

Given a turn of transcript text and a list of already-known entities, identify
every entity mentioned or implied in this turn.

For each entity, return:
- name: the name or description used in the text
- type: one of character, location, faction, item, creature, concept
- is_new: true if this entity is NOT in the known-entities list
- existing_id: if is_new is false, the ID from the known-entities list
- description: one-sentence factual description from THIS turn only
- confidence: 0.0-1.0 confidence that this is a distinct, nameable entity

Rules:
- Only extract entities that appear in the provided turn text.
- Do not invent entities not mentioned in the text.
- Use "the elder" as the name if no proper name is given.
- Generic groups ("your guards") count as a single collective entity.
- Include source_turn in every output object.
- Confidence below 0.5 means the mention is too vague to catalog.

Return a JSON array.
```

**User prompt:**

```
## Current Turn
Turn ID: turn-019
Speaker: dm
Text:
You drop to your knees in the frozen earth, the impact jarring your still-sore
shoulder. The elder surveys you for a long moment, his gaze sharp and assessing.
He then speaks again, a stream of harsh-sounding words directed at your captors,
punctuated by gestures towards you. Your guards respond in kind, explaining
something with animated hands. The elder nods slowly, then steps closer, his eyes
raking over your warlock attire and elven features. He reaches out a gnarled hand,
roughly prodding the bindings on your wrists before gesturing to the fire. A
younger woman emerges from the periphery, carrying a steaming wooden bowl, which
she places before the elder. He plunges a hand into the bowl, pulling out a
handful of some dark, fibrous material, which he then holds out to you.

## Known Entities
(none — empty catalog)
```

**Expected response:**

```json
[
  {
    "name": "The elder",
    "type": "character",
    "is_new": true,
    "existing_id": null,
    "description": "An elderly man with gnarled hands and sharp, assessing gaze who appears to hold authority over the tribal group.",
    "confidence": 0.9,
    "source_turn": "turn-019"
  },
  {
    "name": "The guards",
    "type": "character",
    "is_new": true,
    "existing_id": null,
    "description": "A group of captors/guards who brought the player to the elder and communicate with animated hand gestures.",
    "confidence": 0.7,
    "source_turn": "turn-019"
  },
  {
    "name": "A younger woman",
    "type": "character",
    "is_new": true,
    "existing_id": null,
    "description": "A younger woman who emerged carrying a steaming wooden bowl for the elder.",
    "confidence": 0.6,
    "source_turn": "turn-019"
  },
  {
    "name": "Tribal camp",
    "type": "location",
    "is_new": true,
    "existing_id": null,
    "description": "An outdoor camp with a fire where the elder and tribal group are gathered.",
    "confidence": 0.7,
    "source_turn": "turn-019"
  }
]
```

**Design observations from this prototype:**

1. **Unnamed entities are common in early turns.** The elder doesn't get the name "Shaman" until turn-081. The system must handle provisional names (`"The elder"`) and later merge them when a proper name is revealed. The Entity Detail Extractor handles this by detecting name changes and adding aliases.

2. **Confidence thresholds matter.** "A younger woman" at 0.6 confidence is borderline — she may never appear again. The script layer applies a configurable threshold (default 0.6) below which entities are logged but not cataloged.

3. **Collective entities need a convention.** "The guards" is a group, not a single character. The `id` convention `char-guards-tribal` or `faction-tribal-guards` needs a decision. **Decision: groups of unnamed individuals are typed as `faction` if they act as a unit, or omitted if they are background.**

4. **The player character is implicit.** The "you" in DM turns refers to Fenouille Moonwind. The system must bootstrap the player character entity from metadata or the first player turn. **Decision: the player character is pre-seeded in the catalog from session metadata before extraction begins.**

### 7.3 Coreference resolution example

By turn-081 the catalog contains `char-elder` (provisionally named "The elder"). When the DM first uses the word "Shaman" to refer to this character, the Entity Discovery agent receives:

```
## Known Entities
char-elder | The elder | character
```

The agent returns:

```json
{
  "name": "The Shaman",
  "type": "character",
  "is_new": false,
  "existing_id": "char-elder",
  "description": "...",
  "confidence": 0.85,
  "source_turn": "turn-081"
}
```

The Entity Detail Extractor then adds "The Shaman" as an alias and optionally proposes renaming the display `name` field to "The Shaman" (the script layer handles renaming while preserving the stable `id`).

---

## 8. Module Structure

New files introduced in Phase 2:

```
config/
    llm.json                          # LLM provider configuration

tools/
    llm_client.py                     # LLM API client abstraction
    semantic_extraction.py            # Orchestrator: extract_and_merge, batch, single
    catalog_merger.py                 # Merge agent output into existing catalogs

templates/
    extraction/
        entity-discovery.md           # System prompt for Entity Discovery agent
        entity-detail.md              # System prompt for Entity Detail Extractor
        relationship-mapper.md        # System prompt for Relationship Mapper
        event-extractor.md            # System prompt for Event Extractor
```

No existing files are deleted. Modifications to existing files:

| File | Change |
|---|---|
| `tools/bootstrap_session.py` | Add `extract_semantic_batch()` call after `extract_all()` |
| `tools/ingest_turn.py` | Add `--extract` flag and `extract_semantic_single()` call |
| `requirements.txt` | Add `openai>=1.0.0` as optional dependency |

### 8.1 `tools/semantic_extraction.py` — Core orchestrator

```python
def extract_and_merge(
    turn: dict,              # {turn_id, speaker, text}
    catalogs: dict,          # {characters: [...], locations: [...], ...}
    llm: LLMClient,
    config: dict,            # extraction config (thresholds, flags)
) -> dict:
    """Process one turn through all extraction agents.

    Returns updated catalogs dict.
    """
    # 1. Entity Discovery
    known = format_known_entities(catalogs)
    discovered = llm.extract_json(
        system_prompt=load_template("entity-discovery"),
        user_prompt=format_discovery_prompt(turn, known),
    )
    validate_discovery_output(discovered)

    # 2. Entity Detail Extraction (per entity above threshold)
    for entity_ref in filter_by_confidence(discovered, config["min_confidence"]):
        current_entry = lookup_entity(catalogs, entity_ref)
        updated = llm.extract_json(
            system_prompt=load_template("entity-detail"),
            user_prompt=format_detail_prompt(turn, entity_ref, current_entry),
        )
        validate_against_schema(updated, "entity.schema.json")
        merge_entity(catalogs, updated)

    # 3. Relationship Mapping
    mentioned = get_mentioned_entities(catalogs, discovered)
    if len(mentioned) >= 2:
        relationships = llm.extract_json(
            system_prompt=load_template("relationship-mapper"),
            user_prompt=format_relationship_prompt(turn, mentioned),
        )
        merge_relationships(catalogs, relationships, turn["turn_id"])

    # 4. Event Extraction
    events = llm.extract_json(
        system_prompt=load_template("event-extractor"),
        user_prompt=format_event_prompt(turn),
    )
    merge_events(catalogs, events)

    return catalogs
```

### 8.2 `tools/catalog_merger.py` — Merge logic

The merger handles:

- **New entities:** Validate ID prefix, add to the appropriate catalog array.
- **Existing entities:** Deep-merge `attributes`, append new `relationships`, update `description` only if the new description adds information, update `last_updated_turn`.
- **Name changes / aliases:** If an entity is re-discovered with a different `name`, add the old name to `attributes.aliases` (string, comma-separated) and update `name` to the new value.
- **Conflict resolution:** If the agent produces an entity with an `id` that already exists but a different `type`, log a warning and skip. Type changes are not allowed automatically.
- **Deduplication:** Relationships are deduplicated by `(source_id, target_id, relationship)` tuple. If a relationship already exists, only `last_updated_turn` and `confidence` are updated.

---

## 9. Error Handling and Resilience

### 9.1 LLM failures

| Failure mode | Handling |
|---|---|
| **API timeout** | Retry up to `retry_attempts` times with exponential backoff. Log and skip turn after exhausting retries. |
| **Invalid JSON response** | Log the raw response, skip this agent call for this turn. Do not write partial data. |
| **Schema validation failure** | Log the invalid object, skip this entity/event. Other entities from the same turn are still processed. |
| **Rate limit (429)** | Respect `Retry-After` header. Increase `batch_delay_ms` dynamically. |
| **Provider unavailable** | Skip semantic extraction entirely. Print warning. Existing regex extraction still runs. |

### 9.2 Idempotency

Rerunning extraction on a turn that was already processed does not create duplicates. The merger checks entity `id` before adding. If the entity exists, it performs an update merge. A `--force-reextract` flag on the CLI clears existing semantic data before reprocessing.

### 9.3 Progress tracking (batch mode)

For a 344-turn batch, the script writes progress to `derived/extraction-progress.json`:

```json
{
  "last_completed_turn": "turn-157",
  "total_turns": 344,
  "entities_discovered": 23,
  "errors": []
}
```

If the batch is interrupted, rerunning resumes from `last_completed_turn + 1`.

---

## 10. What This Design Intentionally Excludes

- **Specific prompt wording.** Templates will be iterated during Phase 2 based on empirical results. This document defines the structure and contract, not the final English text.
- **Anomaly detection agent.** Deferred. Anomalies can be derived from entity attributes and events.
- **Cross-session entity merging.** Each session's extraction is independent. Framework-level catalog merging across sessions is future work.
- **Streaming responses.** Not needed — extraction calls are short and structured.
- **Fine-tuning or training.** The system uses general-purpose models with engineered prompts.
- **Copilot-in-editor integration.** While Copilot Chat could theoretically perform extraction via manual interaction, it cannot be invoked programmatically from Python scripts. The API-based approach is automatable and reproducible.

---

## 11. Acceptance Criteria Mapping

| Phase 1 Criterion | Status |
|---|---|
| Design document in `docs/` | This document |
| LLM integration approach decision | §3 — OpenAI-compatible API with pluggable provider |
| Agent specialization boundaries decision | §4 — Four roles: Discovery, Detail, Relationship, Event |
| Batch vs. incremental invocation strategy decision | §5 — Unified pipeline, mode selected by caller |
| Privacy model for transcript content | §6 — Per-provider breakdown, local-first recommendation |

---

## 12. Phase 2 Implementation Plan

Recommended implementation order:

1. **`config/llm.json` + `tools/llm_client.py`** — Get a working API call with JSON response parsing. Test against Ollama and OpenAI.
2. **`templates/extraction/entity-discovery.md`** — Write and test the discovery prompt against sample turns (turn-001, turn-019, turn-100, turn-300).
3. **`tools/semantic_extraction.py`** — Implement `extract_and_merge` with only Entity Discovery wired up. Validate that running it over session-import produces a non-empty `characters.json`.
4. **`templates/extraction/entity-detail.md` + detail extraction** — Add the detail extractor. Verify entity entries conform to `entity.schema.json`.
5. **`templates/extraction/relationship-mapper.md` + relationship extraction** — Add relationship mapping. Verify `relationships[]` arrays are populated.
6. **`templates/extraction/event-extractor.md` + event extraction** — Add event extraction.
7. **`tools/catalog_merger.py`** — Robust merge with alias handling and deduplication.
8. **Integration into `bootstrap_session.py` and `ingest_turn.py`** — Wire into existing entry points.
9. **Validation and testing** — Run against full session-import. Compare with hand-annotated sample. Run `tools/validate.py`.
