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
| Evidence agent | Tag and classify claims; maintain evidence.json | **Implemented** (#259) |
| Strategy agent | Generate next-move analysis; apply heuristics and risk model | — |
| Prompt agent | Generate candidate player prompts optimized per mode | — |
| DM profile agent | Infer and refine DM behavior from accumulated evidence | **Implemented** (#260) |
| Timeline agent | Extract temporal signals and estimate in-game time progression | **Implemented** (#137) |
| Story summary agent | Generate narrative arc summary from extracted entities, events, and plot threads | **Implemented** (#262) |
| Planning layer | Synthesize catalog data into actionable derived planning files | **Implemented** (#259) |
| VS Code agent bridge | Delegate tasks to VS Code Copilot agents via HTTP bridge | **Implemented** (#346) |

The **Catalog agent** is implemented as `tools/semantic_extraction.py` — a five-agent LLM pipeline (Entity Discovery → Entity Detail → Relationship Mapper → Event Extractor → Temporal Signal Extractor) that runs during bootstrap and incremental ingestion. It uses prompt templates in `templates/extraction/` and a provider-agnostic LLM client (`tools/llm_client.py`) supporting OpenAI and Ollama.

Post-extraction quality passes include:
- **Dedup** — name, token-overlap, ID-stem, and Levenshtein matching (with minimum 6-char stem guard, #132)
- **Stub backfill** — re-extracts hollow stub entities using gathered context; runs by default (#128, #131)
- **PC alias merge** — detects character entities that are aliases of char-player and merges them (#134); rejects meta-labels ("player character", "pc", etc.) via a blocklist (#186)
- **PC consecutive-failure logging** — warns when PC extraction fails for ≥10 consecutive turns; cooldown-based skip after 20 failures (50 turn skip, 5 turn retry cycle) (#133, #168)
- **Rate limit handling** (#215) — classifies API errors by HTTP status code, logs `Retry-After` header presence, uses exponential backoff with jitter for cloud providers, disables SDK-level retries to prevent 3×3 retry multiplication, auto-detects cloud providers and enforces 2000ms minimum inter-call delay, stops extraction when consecutive 429 errors exceed a configurable threshold (`consecutive_rate_limit_threshold`, default 10), treats `RESOURCE_EXHAUSTED` as a 429 equivalent, and reports per-run API statistics (total requests, error breakdown, Retry-After headers seen)
- **Entity envelope unwrapping** (#168) — accepts both wrapped and flat entity responses from the LLM, fixing PC extraction regression with smaller models
- **Non-standard key coercion** (#170, #172, #178) — remaps common non-standard LLM keys into their correct V2 schema slots before validation. Extended in #172 to cover 26+ additional keys observed in Run 10a, including `_new` suffix normalization for diff-format LLM outputs. Null values in `stable_attributes` are stripped before validation (#178).
- **Relationship dedup** (#183) — `_dedup_relationships()` consolidates duplicate relationship entries sharing the same `target_id`, merging history arrays and keeping the most recently updated entry
- **Dangling relationship cleanup** (#184) — `cleanup_dangling_relationships()` removes relationships targeting entities that no longer exist in any catalog; runs after dedup in both batch and segmented pipelines; `validate_extraction.py` reports remaining dangling targets as warnings
- **Reverse relationship index** (#258) — `generate_relationship_index()` builds a bidirectional index mapping each entity to its forward and reverse relationships. Auto-generated during `save_catalogs()` as `framework/catalogs/relationship-index.json`. Enables efficient reverse lookups ("what entities reference this target?") without scanning all entity files
- **Late-game entity coverage** (#185) — type-specific orphan thresholds (locations: 2 refs, factions: 1 ref, characters: 3 refs), turn-tag ID normalization (`char-shaman-turn-082` → `char-shaman`), expanded season enum with sub-seasons, season coercion for LLM output variants, and name-mention discovery sweep that creates stubs for proper names appearing in 2+ event descriptions but missing from catalogs. Ground truth fixture supports glob `id_patterns`.
- **Extraction validation** — post-extraction ground truth comparison that catches false alias merges, missing entities, coreference fragmentation, and staleness (#159). Uses curated fixtures in `tests/fixtures/` and runs via `tools/validate_extraction.py`.
- **Periodic entity refresh** (#161, #182) — re-extracts stale entities every N turns (`entity_refresh_interval`, default 50) using recent transcript context. Up to `entity_refresh_batch_size` (default 10) entities are refreshed per interval, with dynamic scaling for large catalogs (60+ entities scale to `catalog_size // 5`, capped at 25). Type-aware slot allocation gives characters 50% of refresh slots, locations 20%, items 20%, and factions 10%, with overflow redistribution. Event-frequency tiebreaking prioritizes narratively important entities.
- **Scene graph / spatial index** (#257) — cross-type queryable index (`framework/catalogs/scene-graph.json`) built from entity catalogs by `tools/build_scene_graph.py`. Location index maps location IDs to present entities, turn activity index maps turns to active entity IDs, and location connections track spatial edges between locations. Integrated into `build_context.py` for O(T) nearby-entity lookups. Additive capability — no re-extraction required.
- **Context-aware entity selection** (#233) — replaces recency-only entity context selection with multi-tier context-aware selection. When the current turn's text is available, `format_known_entities_bounded()` prioritizes: (1) mentioned entities (name/alias keyword match), (2) co-located entities (shared `volatile_state.location` or at a mentioned location), (3) one-hop relationship targets, (4) recency backfill. Context-relevant entities receive full detail regardless of recency window. Budget enforcement from #221 is preserved. Field measurements showed recency-only selection filling the budget with narratively distant entities while dropping location-relevant ones — context-aware selection fixes this and can cut prompt size by 50-60% on constrained hardware.

**Timeline tracking** (#137, #263): The pipeline extracts temporal signals (season transitions,
biological markers, construction milestones) and estimates in-game day offsets from a
configurable reference anchor. Implemented in `tools/temporal_extraction.py` with
pattern-based detection plus an optional LLM template. Integrated into the semantic
extraction pipeline (#263) as Phase 5: `extract_temporal_signals()` runs per-turn after
event extraction, signals are merged with dedup into `framework/catalogs/timeline.json`,
and the timeline is saved at checkpoints and reconciled across segments. The extraction
log records `temporal_ok`, `temporal_error`, and `new_temporal_signals` per turn.
Also integrated into wiki page display (estimated day column in event timelines,
season-enriched infoboxes).

**Narrative timeline summary** (#275): The timeline wiki page (`framework/catalogs/timeline.md`)
presents temporal data as a narrative. Includes: anchor date / current position infobox,
5-15 sentence natural-language temporal arc summary, and filtered reference tables.
Season flicker filtering removes low-confidence noise (isolated regex false positives).
Anchor event auto-detection selects the most significant early event as reference point.

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

Completed:
- **Fallback provider chain** (#301): When the primary LLM exhausts all `retry_attempts`, the client automatically routes to a fallback provider configured via the `"fallback"` block in `config/llm.json`. `LLMTruncationError` and `QuotaExhaustedError` bypass the fallback and propagate immediately.
- **OpenVINO GenAI REST server** (#299): `server/ov_serve.py` provides an OpenAI-compatible `/v1/chat/completions` endpoint using `ContinuousBatchingPipeline` with prefix caching, dynamic batching, and thinking suppression for Intel Arc GPUs.
- **Context-aware entity selection** (#296): Multi-tier entity context selection for discovery prompts — mentioned entities, co-located entities, one-hop relationships, then recency backfill — replacing recency-only ordering.
- **Entity type classification guidance** (#305): The entity discovery template includes a compact type classification guide with positive examples and explicit NEVER-use rules, plus a post-discovery programmatic filter that rejects misclassified entities.
- **Alias conflict rejection guard** (#307): `_filter_pc_aliases()` and `_filter_entity_aliases()` cross-reference aliases against all entity names in the catalog, rejecting any alias that matches another entity's primary name.
- **Compound-term fragment rejection** (#398): `_build_compound_word_index()` builds a runtime index of every word in every multi-word entity name (normalized to `[a-z]+`); `_is_compound_term_fragment()` rejects single-word entities whose name appears in the index. The prompt template (`entity-discovery.md`) reinforces this with an ENTITY NAME VALIDATION section. The index is built from catalog data at runtime — no hardcoded word lists. A/B validation was completed as part of the PR #399 templates process.
- **LLM-assisted dedup audit** (#306): `tools/dedup_audit.py` identifies duplicate entities via name similarity heuristics, scores candidate pairs with an LLM call, auto-merges high-confidence duplicates (≥0.9) or flags medium-confidence pairs for human review.
- **Discovery output optimization** (#310): Rewritten discovery template with two-tier output format (compact 2-field for known entities, full for new/changed entities) and post-discovery expansion of compact entries from catalogs. Reduces output tokens by up to 60% for entity-dense turns. Includes `tools/discovery_baseline.py` measurement harness for A/B testing template changes.
- **Context budget for relationship mapper and entity detail** (#385): Extends the discovery budget architecture to the two remaining unbounded context growth vectors. Relationship relevance scoring uses 3-tier priority with 20% token budget. Arc-aware compression generalizes volatile digest to all entities. Scene-scoped detail trims non-PC entries. All controlled via `context_optimizations` in `config/llm.json` (default off). A/B testing showed 28% faster turns, 78% fewer relationship mapper tokens, and elimination of context overflows with equal or better extraction quality.
- **Thinking suppression + fallback JSON parser** (#300): Robust output parsing strips `<think>` blocks, handles markdown code fences, and scans for valid JSON objects when initial parse fails.
- Batch processing mode for unattended overnight extraction (**implemented** via detached helper scripts: `tools/start_extraction_detached.ps1`, `tools/watch_extraction_detached.ps1`, `tools/stop_extraction_detached.ps1`; launcher safely quotes spaced arguments and supports framework/player-label passthrough)
- Provider setup documentation in `docs/usage.md`
- **Segmented extraction** (#141, #197): Long sessions are extracted in configurable segments to stay within model context limits. Each segment starts with a clean entity catalog; a reconciliation pass merges the results. The bootstrap default now auto-enables `--segment-size 100` when session size exceeds 150 turns (pass `--segment-size 0` to disable).

Remaining work:
- CLI `--provider` override for per-run provider selection
- Quality validation of `qwen2.5:7b` as a faster alternative to 14B
- **Per-turn token stabilization**: Per-turn extraction input tokens grow linearly and unbounded with session length (measured `15182 + 88.62 * turn`, ~17K at t1-30 -> ~43K at t300-344 in `eval-qwen36-344t-full`, no plateau). `entity_detail` is 64% of the load and most of the slope, driven by the uncapped PC relationship web, detail-call-count growth, and per-call template+turn repetition. The design report `docs/design-token-stabilization.md` decomposes the runaway per phase, maps four levers (L1 PC type-tiered relationship cap, L2 batch entity_detail, L3 detail selection-fix, L4 relmap budget tiering) to the token slice each attacks with honest expected-return ESTIMATE ranges, and projects that L1+L4+L2 roughly halve the slope but do **not** achieve bounded growth without an additional architectural lever (summary-based catalog state / retrieval-scoped injection / per-phase hard caps). Extends the #385 context-budget work, which capped non-PC detail and relmap but left the PC web uncapped. Includes a dashboard data spec for an estimate-vs-measured "Token Budget / Lever Analysis" panel.
- **Bounded per-turn context architecture (A1+A2)** (epic #477): The design report `docs/design-context-architecture-bounded.md` specifies a complementary two-part architecture that targets *bounded* per-turn extraction token demand (where the token-stabilization levers L1/L4/L2 only halve the slope). A1 is a bounded, rolling/hierarchical summary-based running state — a compact always-in-context identity/alias index (the coreference anchor) plus a fixed-budget salient-state digest (arcs, unresolved relationships) and verbatim current-status — and includes **intelligent forgetting**: dormant entities that are neither quantitatively (mention count / centrality) nor semantically (permanent-bond / key-plot) important are rolled up and evicted from the active index, with promote-on-mention restoring them so forgetting stays reversible and coreference-safe. A2 is retrieval-scoped full-fidelity injection of the turn-relevant subset (entities mentioned this turn + 1-hop neighborhood + active relationships), reusing the #233 relevance selection. The report rejects per-phase hard caps as a stabilization strategy (caps are only an emergency backstop at the LLM context limit, not a substitute for intelligent reduction), shows how A1 and A2 cover each other's blind spots (A1 anchors long-dormant re-mentions so no duplicate is minted; A2 restores fidelity for the current turn's focus entities), and frames coreference safety as a **design guarantee gated by a long-run zero-new-duplicate A/B** (the identity anchor is never dropped, unlike the rejected #468 catalog trimming) rather than a proven result. Rather than demanding upfront answers to open design forks (digest method, rollup tiers, retrieval backend, eviction thresholds), it adopts a **learn-as-you-go, telemetry-driven** approach: build for both options where unknown, instrument the deciding signal, and choose once data exists. It recommends shipping L1/L4/L2 first, then A1 (the coreference safety floor) then A2, validated by a long-run (>=150t, ideally the full 344t) A/B. Builds directly on the per-phase/per-lever decomposition in `docs/design-token-stabilization.md` (PR #478).
- **Quality Evaluation Pipeline** (epic #97): Automated LLM-as-judge scoring, composite quality metric, and dedup blocking gate for model-vs-model comparisons. The methodology is defined in `docs/model-eval-standard.md` (#430). Complements `docs/ab-test-standard.md` (template changes) by providing an equivalent standard for model selection decisions.

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
