# Roadmap

This document describes the intended development trajectory of narrative-state-engine.

The goal at each phase is to keep the system **practical and usable**, not to accumulate features prematurely.

---

## Phase 1 — Current: VS Code Copilot Chat

**Status:** In progress.

The primary workflow is a single user running the repo locally in VS Code with GitHub Copilot chat.

- Manual turn ingestion via `tools/ingest_turn.py` or `tools/bootstrap_session.py`
- Copilot-assisted state updates (catalogs, evidence, objectives, DM profile)
- Python tools for scaffolding and validation
- GitHub issues assignable to Copilot for async tasks

**Limitations at this phase:**
- State extraction (evidence, objectives, DM profile) requires Copilot assistance; it is not fully automated
- Entity/relationship/event extraction is automated via LLM (see Phase 2 progress below) but other state updates remain manual
- Single-user, single-session workflow
- No automated pipeline between tool steps

---

## Phase 2 — Agent Orchestration (v2)

Divide session processing across multiple specialized agents, each with a focused scope:

| Agent | Responsibility | Status |
|---|---|---|
| Ingestion agent | Parse raw turns, write transcript files, update full-transcript.md | — |
| Catalog agent | Extract and maintain entities, locations, factions, items | **Implemented** (#43) |
| Evidence agent | Tag and classify claims; maintain evidence.json | — |
| Strategy agent | Generate next-move analysis; apply heuristics and risk model | — |
| Prompt agent | Generate candidate player prompts optimized per mode | — |
| DM profile agent | Infer and refine DM behavior from accumulated evidence | — |
| Timeline agent | Extract temporal signals and estimate in-game time progression | **Implemented** (#137) |

The **Catalog agent** is implemented as `tools/semantic_extraction.py` — a four-agent LLM pipeline (Entity Discovery → Entity Detail → Relationship Mapper → Event Extractor) that runs during bootstrap and incremental ingestion. It uses prompt templates in `templates/extraction/` and a provider-agnostic LLM client (`tools/llm_client.py`) supporting OpenAI and Ollama.

Post-extraction quality passes include:
- **Dedup** — name, token-overlap, ID-stem, and Levenshtein matching (with minimum 6-char stem guard, #132)
- **Stub backfill** — re-extracts hollow stub entities using gathered context; runs by default (#128, #131)
- **PC alias merge** — detects character entities that are aliases of char-player and merges them (#134); rejects meta-labels ("player character", "pc", etc.) via a blocklist (#186)
- **PC consecutive-failure logging** — warns when PC extraction fails for ≥10 consecutive turns; cooldown-based skip after 20 failures (50 turn skip, 5 turn retry cycle) (#133, #168)
- **Entity envelope unwrapping** (#168) — accepts both wrapped and flat entity responses from the LLM, fixing PC extraction regression with smaller models
- **Non-standard key coercion** (#170, #172, #178) — remaps common non-standard LLM keys into their correct V2 schema slots before validation. Extended in #172 to cover 26+ additional keys observed in Run 10a, including `_new` suffix normalization for diff-format LLM outputs. Null values in `stable_attributes` are stripped before validation (#178).
- **Relationship dedup** (#183) — `_dedup_relationships()` consolidates duplicate relationship entries sharing the same `target_id`, merging history arrays and keeping the most recently updated entry
- **Dangling relationship cleanup** (#184) — `cleanup_dangling_relationships()` removes relationships targeting entities that no longer exist in any catalog; runs after dedup in both batch and segmented pipelines; `validate_extraction.py` reports remaining dangling targets as warnings
- **Late-game entity coverage** (#185) — type-specific orphan thresholds (locations: 2 refs, factions: 1 ref, characters: 3 refs), turn-tag ID normalization (`char-shaman-turn-082` → `char-shaman`), expanded season enum with sub-seasons, season coercion for LLM output variants, and name-mention discovery sweep that creates stubs for proper names appearing in 2+ event descriptions but missing from catalogs. Ground truth fixture supports glob `id_patterns`.
- **Extraction validation** — post-extraction ground truth comparison that catches false alias merges, missing entities, coreference fragmentation, and staleness (#159). Uses curated fixtures in `tests/fixtures/` and runs via `tools/validate_extraction.py`.
- **Periodic entity refresh** (#161, #182) — re-extracts stale entities every N turns (`entity_refresh_interval`, default 50) using recent transcript context. Up to `entity_refresh_batch_size` (default 10) entities are refreshed per interval, with dynamic scaling for large catalogs (60+ entities scale to `catalog_size // 5`, capped at 25). Type-aware slot allocation gives characters 50% of refresh slots, locations 20%, items 20%, and factions 10%, with overflow redistribution. Event-frequency tiebreaking prioritizes narratively important entities.

**Timeline tracking** (#137): The pipeline extracts temporal signals (season transitions,
biological markers, construction milestones) and estimates in-game day offsets from a
configurable reference anchor. Implemented in `tools/temporal_extraction.py` with
pattern-based detection plus an optional LLM template. Integrated into wiki page display
(estimated day column in event timelines, season-enriched infoboxes).

Benefits:
- Narrower per-agent context window → lower token cost
- Agents can run in parallel on unrelated subtasks
- Issues can be assigned to specialized agents rather than one general-purpose one

---

## Phase 3 — Local LLM Workflows (v3)

Replace cloud AI calls with a locally-run LLM on a GPU.

Goals:
- Token-free (no cloud cost per session turn)
- Offline-capable (no internet dependency during play)
- Faster turnaround for frequent small-context tasks (catalog updates, summary refreshes)

**Status:** Partially achieved. The semantic extraction pipeline (#43) already supports local models via Ollama. Tested with `qwen2.5:14b` on RTX 4070 at 60.61 tok/s (acceptable quality) and `qwen2.5:3b` (unusable quality — see #53, #63). The `config/llm.json` design decouples the pipeline from any specific provider. Context window configuration primarily uses Modelfile variants; the code also sends `context_length` as `extra_body.options.num_ctx` (#175), but Ollama's OpenAI-compatible `/v1` endpoint may ignore that runtime override.

**NPU investigation (#65):** AMD XDNA1 (Phoenix) NPU cannot run LLM inference — AMD only supports LLMs on Strix Point (XDNA2) and newer. The Radeon 780M iGPU (~10-15 tok/s) is too slow to be useful. A dedicated GPU server (e.g., used RTX 3090 in a separate machine) is the viable path to exceed RTX 4070 performance.

Remaining work:
- Fallback provider chain in `tools/llm_client.py` (local → cloud)
- CLI `--provider` override for per-run provider selection
- Batch processing mode for unattended overnight extraction (**partially implemented** via detached helper scripts: `tools/start_extraction_detached.ps1`, `tools/watch_extraction_detached.ps1`, `tools/stop_extraction_detached.ps1`)
- Provider setup documentation in `docs/usage.md`
- Quality validation of `qwen2.5:7b` as a faster alternative to 14B
- **Segmented extraction** (#141): Long sessions (300+ turns) are extracted in configurable segments to stay within model context limits. Each segment starts with a clean entity catalog; a reconciliation pass merges the results. Naturally parallelizable across GPU instances.

Design implications for earlier phases:
- Keep context loading modular (catalog-first) so smaller local models can handle targeted tasks
- Keep prompt templates well-structured so they work with less capable models
- Avoid tight coupling to any single cloud provider API
- Minimum model size: 7B+ parameters for structured extraction (see #53)

---

## Phase 4 — Fiction Authoring / Book Export (v4)

Transform accumulated session transcripts and derived state into fiction-ready material.

Planned capabilities:
- `tools/export_book_skeleton.py` — generate a rough book structure from a session:
  - Premise
  - Acts and major beats
  - Character arcs
  - Unresolved narrative threads
- Per-session `exports/book-skeleton.md` placeholder (already scaffolded)
- Prose-oriented summary mode (separate from the strategy-oriented session summary)

This phase does not require changes to the raw/derived data model; it reads from existing artifacts.

---

## Future Considerations

- **Optional DM mode**: the system acts as a DM for solo or offline play (requires significant new work)
- **Multi-world support**: a higher `world` or `campaign` grouping above `session`, if needed
- **Web UI**: a lightweight local UI for turn ingestion and state review without VS Code
- **Shared framework repos**: allow forked instances to pull framework-level heuristics and templates from an upstream without sharing session content

---

## Principles that must hold across all phases

1. Raw transcript files remain immutable in all phases.
2. Every derived artifact must remain traceable to source turns.
3. The system must remain usable manually (without automation) as a fallback.
4. Session content (transcripts, story artifacts) is never mixed with framework code licensing.
