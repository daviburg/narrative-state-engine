# Extraction Architecture Review

> Engineering-level assessment of the narrative-state-engine extraction pipeline.
> Date: 2026-04-24 | Status: Research document — no code changes proposed

---

## 1. Problem Characterization

The extraction pipeline solves a **closed-corpus incremental knowledge graph construction** problem. A single narrative document (~345 turns, ~100K words) must be processed sequentially to produce a structured world model: a typed entity catalog with attributes, inter-entity relationships, and an event log, all with provenance back to source turns.

This sits at the intersection of three established fields:

1. **Information Extraction (IE)**: Identifying entities, relations, and events from unstructured text. The classic NLP pipeline (NER → coreference resolution → relation extraction → event detection) maps directly to the four-agent architecture.

2. **Knowledge Graph Construction (KGC)**: Building and maintaining a structured knowledge base from extracted facts. The catalog system — entities with typed relationships, temporal provenance, and confidence scores — is a knowledge graph with a file-based backing store.

3. **Incremental / Streaming IE**: Processing a document sequentially where each extraction step must incorporate prior state. This is distinct from batch document IE because the known-entity list grows with each turn, creating a feedback loop between extraction state and extraction capability.

The problem has several properties that distinguish it from standard IE benchmarks:

- **Narrative coreference is harder than newswire coreference.** The same character may be referred to as "the elder", "Fern", "the shaman", "the old woman", or simply implied by context. Fantasy names have no pretrained entity priors.
- **The document is stateful.** A character captured in turn-007 is freed in turn-029. The extraction system must track state transitions, not just entity mentions.
- **Scale is moderate for text but large for LLM call volume.** 345 turns × 4 agents = ~2,000 LLM calls minimum. This is a systems engineering problem, not a pure NLP problem.
- **Ground truth is sparse.** There is no labeled training set. Validation relies on curated ground-truth files and manual inspection.

The closest analogues in the literature and in practice are:

- **Incremental knowledge base population** (TAC-KBP tasks): Extract entities and relations from a stream of documents into a growing KB. The key difference is that TAC-KBP operates on independent documents, while this pipeline processes a single narrative where later turns assume prior context.
- **Long-document summarization with entity tracking**: Systems like BookSum or narrative understanding benchmarks face similar entity continuity challenges, but target summaries rather than structured extraction.
- **AI memory systems** (MemGPT, LangChain memory): These manage growing context for conversational AI — a related but different problem where the output is a conversational response rather than a structured knowledge base.

---

## 2. Current Architecture Assessment

### 2.1 Architecture as Implemented

The pipeline processes turns sequentially through a four-agent LLM pipeline, accumulating state in an in-memory catalog that is periodically checkpointed to disk.

**Actual data flow per turn:**

```
Turn text (from transcript)
    │
    ├──[1]──► Entity Discovery (LLM)
    │           Input: turn text + known-entity table (all entities so far)
    │           Output: list of (entity_id, name, type, is_new, confidence)
    │
    ├──[2]──► Entity Detail Extraction (LLM, 1 call per discovered entity)
    │           Input: turn text + entity ref + prior catalog entry
    │           Output: full entity object (V2 schema)
    │           Special path: char-player always runs with extended timeout
    │
    ├──[3]──► Relationship Mapper (LLM, skipped if <2 entities)
    │           Input: turn text + mentioned entities + existing relationships
    │           Output: relationship edges consolidated per (source, target) pair
    │
    └──[4]──► Event Extractor (LLM)
                Input: turn text + qualified entity IDs + next event ID
                Output: event objects with related_entities and provenance
    │
    ▼
    Coercion → Validation → Merge into in-memory catalogs
    │
    ├── every 25 turns: checkpoint progress to extraction-progress.json
    ├── every 50 turns: refresh stale entities (re-extract with recent context)
    ├── end of run: final refresh pass + orphan sweep + dedup + name-mention discovery
    └── save catalogs to per-entity files + index.json
```

**Post-extraction passes:**

1. `_dedup_catalogs()` — fuzzy entity deduplication (substring, token overlap, Levenshtein)
2. `_post_batch_orphan_sweep()` — create stubs for event-referenced IDs with no catalog entry
3. `_name_mention_discovery()` — scan event descriptions for proper names absent from catalogs
4. `backfill_stubs()` — re-extract stubs with gathered context
5. `cleanup_dangling_relationships()` — remove references to non-existent entities
6. `mark_dormant_relationships()` — mark old relationships as dormant
7. `_merge_pc_aliases()` — merge entities that are actually PC aliases

### 2.2 Assumptions and Where They Break

#### Assumption 1: The known-entity table fits in the model's context window alongside the turn text and system prompt

The entity discovery template sends a compact table of all known entities to the LLM on every turn. This table grows monotonically.

**Where it breaks (Qwen 14B, 8K context):**

By turn 100, the known-entity table reaches ~2,900 tokens. Combined with the system prompt (~800 tokens) and turn text (~500–2,000 tokens), the total input exceeds 8,192 tokens. Evidence from Run 12: entity discovery found 27 new entities in turns 1–25 but zero new entities in turns 126–345 (see [templates exploration, issue #106](#) — 219 consecutive turns with no discovery). The model's output quality collapsed as the input exceeded its effective context window.

**Where it holds (Gemini 2.5 Flash, 1M context):**

With a 1M-token context window, the known-entity table never becomes a constraint. Gemini continued discovering entities through turn 301. The failure mode was infrastructure (API quota exhaustion), not context.

**Implication:** The pipeline's core loop — send all known entities every turn — works for large-context models but is structurally incompatible with models that have context windows under ~32K tokens (see [semantic_extraction.py `format_known_entities()`](../tools/semantic_extraction.py)).

#### Assumption 2: Per-turn extraction accumulates accurately over 300+ turns

The pipeline processes each turn independently, merging results into a growing catalog. This assumes extraction quality remains stable as the catalog grows and the pipeline state becomes more complex.

**Where it breaks:**

- **Coercion load scales with turns.** Run 12 (Qwen) logged 38 unique coercion types. Each coercion is an ad-hoc fix for a model output that doesn't match the schema. At 345 turns × ~3 entities per turn, the coercion layer processes ~1,000 entity responses. The coercion map in [semantic_extraction.py lines 340–430](../tools/semantic_extraction.py) contains ~50 remappings — evidence that the model's output format diverges significantly from the schema.
- **Entity staleness is structural.** An entity first seen at turn-010 and not mentioned again until turn-300 has a 290-turn gap. The periodic refresh (every 50 turns) mitigates this but cannot prevent it — the refresh batch size (10–25 entities) is smaller than the number of entities that go stale. Run 12 (Gemini) showed 25 entities with 50+ turn staleness gaps at run end (see [run reports](#)).
- **Relationship accumulation is quadratic in the worst case.** The `char-player` entity accumulated 37 relationships including 14 near-synonyms with a single NPC (documented in [design-catalog-v2.md](design-catalog-v2.md)). Per-pair consolidation (added post-V2) reduces this but depends on the LLM consistently using `current_relationship` rather than creating new entries.

**Where it works:**

- Event extraction quality remains stable throughout the run. Gemini produced 404 events across 301 turns with consistent structure.
- Schema validation catches most malformed output — Gemini achieved zero parse failures across ~3,000 API calls.

#### Assumption 3: Agent failures are transient and recoverable

The pipeline treats LLM failures (timeouts, HTTP errors, malformed output) as per-turn failures that can be retried later.

**Where it breaks (Gemini, quota exhaustion):**

API quota exhaustion is not transient within a run. Turns 302–344 all failed and were marked as processed with empty output. The `failed_turns` tracking (issue #211) addresses the detection problem but not the fundamental issue: a 344-turn run consumes ~3,000 API calls, which can exhaust Tier 1 daily quotas. On resume, the same quota may still be exhausted.

**Where it breaks (Qwen, progressive degradation):**

PC extraction had a 100% validation failure rate across 62 attempts. This is not a transient failure — it is a systematic incompatibility between the PC's context size and the model's capability. The cooldown mechanism (skip 50 turns, retry 5) assumes the failure is temporary, but the PC's context only grows over time (see [run12 report — PC stalls at turn-054](../framework-local/test-report-run12.md)).

### 2.3 What the Pipeline Does Well

These are strengths that should be preserved in any redesign:

1. **Schema-first design.** Every output is validated against a JSON schema before persistence. This catches most LLM drift and provides a clear contract between extraction and downstream consumers. The 17 schemas in `schemas/` define a rigorous data model.

2. **Provenance tracking.** Every entity, relationship, and event carries `first_seen_turn` and `last_updated_turn`. This is unusual for extraction systems and enables temporal reasoning downstream.

3. **Provider-agnostic LLM client.** The `llm_client.py` abstraction (OpenAI, Ollama, Gemini) means the pipeline can run locally or in the cloud with a config change. This is a genuine architectural strength.

4. **Four-agent specialization.** Splitting extraction into discovery, detail, relationships, and events is sound. Each agent has a focused task with clear inputs and outputs. The alternative — a monolithic "extract everything" prompt — would produce shallower results.

5. **V2 catalog structure.** The per-entity file layout with index files, the stable/volatile attribute split, and the identity/current_status separation are well-designed for downstream consumption. The context builder pattern (focused per-turn entity context) is a practical solution to the "catalogs too large for agent context" problem.

6. **Defensive coercion layer.** While the coercion maps are evidence of model non-compliance, the layer itself is valuable. It normalizes 50+ field name variants, handles envelope format differences, strips noise keys, and remaps event types. Without it, the Qwen run would have produced even fewer valid entities.

7. **Post-batch recovery passes.** The orphan sweep, name-mention discovery, and stub backfill collectively recover entities that the main extraction loop missed. These are pragmatic compensations for LLM extraction gaps.

---

## 3. Landscape Survey

### 3.1 Structured Extraction from Long Documents

| Approach | Core Mechanism | Typical Scale | Tradeoffs |
|----------|---------------|---------------|-----------|
| **Sliding window with overlap** | Process fixed-size chunks with N-token overlap; merge cross-chunk entities | 100K+ tokens | Simple to implement; miss cross-chunk relationships; merge logic required |
| **Hierarchical extraction** | Extract per-chunk, then run a consolidation pass over chunk-level outputs | 100K–1M tokens | Two-pass cost; consolidation quality depends on first-pass quality |
| **Map-reduce extraction** | Parallel extraction per chunk, reduce phase merges | Arbitrary | Parallelizable; no sequential state; harder to track entity continuity |
| **Retrieval-augmented extraction** | For each chunk, retrieve relevant prior context from a vector store rather than sending all state | Arbitrary | Context stays focused; requires embedding infrastructure; retrieval quality varies |
| **Full-context single pass** | Send entire document to a large-context model | Up to model limit (1M+) | Simplest; limited by model context; expensive; quality degrades in long contexts |
| **Iterative refinement** | Multiple passes over the same text with different extraction foci | Any | Higher quality per entity; multiplicative cost; diminishing returns after 2–3 passes |

**Assessment:** The current pipeline uses a **turn-by-turn sequential** approach that is closest to sliding window without overlap, but with an important difference: it maintains a growing state (the known-entity list) that is passed forward on every step. This is more sophisticated than naive sliding window but creates the context growth problem documented in Run 12.

### 3.2 Growing Knowledge Bases During Extraction

| Approach | Core Mechanism | Tradeoffs |
|----------|---------------|-----------|
| **Full state forwarding** (current approach) | Send entire entity roster to each extraction call | Simple; context grows unboundedly; breaks small models |
| **Relevant-entity retrieval** | Embed entity descriptions; retrieve top-K most similar to current chunk | Context stays bounded; requires embeddings; may miss relevant entities not textually similar |
| **Tiered entity context** | Send full details for recently-active entities, summaries for others, omit dormant | Context bounded; heuristic may drop relevant entities |
| **Entity index + on-demand lookup** | Send only entity names/IDs; LLM requests full details for specific entities it needs | Minimal context; requires multi-turn LLM interaction or tool-use capability |
| **External memory (MemGPT pattern)** | Entity state stored outside context; LLM uses tool calls to read/write entities | Bounded context; requires tool-use capable model; adds latency per lookup |

**Assessment:** The pipeline's `format_known_entities()` function implements full state forwarding — the simplest approach and the one that breaks first at scale. The Qwen failure (zero discovery after turn 126) is a textbook example of why full-state forwarding doesn't scale with small context windows. The V2 design's context builder partially addresses this for downstream analysis but doesn't help the extraction loop itself.

### 3.3 Entity Coreference and Deduplication at Scale

| Approach | Core Mechanism | Tradeoffs |
|----------|---------------|-----------|
| **In-context coreference** (current approach) | LLM resolves coreference during extraction by comparing mentions to known-entity list | Zero infrastructure; depends on LLM + context budget; degrades as entity list grows |
| **Post-hoc deduplication** (current, as supplement) | Fuzzy matching on entity names/IDs after extraction | Catches misses; heuristic; false positives (merge distinct entities) |
| **Embedding-based clustering** | Embed entity descriptions; cluster by cosine similarity; merge clusters | Scales well; requires embedding model; threshold tuning needed |
| **Canonical name resolution** | Maintain alias → canonical mapping; resolve before extraction | Fast; requires known aliases; doesn't handle novel coreferences |
| **Cross-document entity linking** | Match extracted entities against a reference KB (Wikidata, etc.) | High precision for known entities; useless for fictional characters |
| **Manual coreference hints** (current, as supplement) | User-provided merge rules in `coreference-hints.json` | Perfect precision; doesn't scale; requires user effort |

**Assessment:** The pipeline uses a layered strategy: in-context coreference (primary), post-hoc fuzzy dedup (secondary), and manual hints (tertiary). This is a reasonable approach but the primary mechanism (in-context) fails when context is exhausted, and the secondary mechanism (fuzzy dedup) runs only at the end of the batch — meaning intermediate state is fragmented.

### 3.4 Maintaining Extraction Quality as Context Grows

| Approach | Core Mechanism | Tradeoffs |
|----------|---------------|-----------|
| **Context pruning** | Progressively trim less-relevant state from the prompt | Bounded context; may drop entities that become relevant later |
| **Summarized state** | Replace detailed entity descriptions with compressed summaries in the discovery prompt | Reduces tokens per entity; lossy; summary quality matters |
| **Segment-then-reconcile** (current, `--segment-size`) | Process in independent segments, merge afterward | Fresh context per segment; reconciliation is complex; cross-segment entities may duplicate |
| **Progressive distillation** | After N turns, "distill" the full catalog into a compact representation and restart | Bounded growth; distillation is lossy; provenance continuity harder |
| **Adaptive context** | Monitor prompt size; switch strategies when approaching limits | Resilient; complex to implement; model-specific |
| **Two-tier models** | Use a small model for routine turns, escalate to a large model for complex turns or periodically | Cost-efficient; requires turn complexity estimation; adds infrastructure |

**Assessment:** The pipeline's `--segment-size` option (segment-then-reconcile) is the current solution for long sessions. This is a valid approach but introduces a reconciliation problem: entities discovered in segment 2 may duplicate entities from segment 1 under different names. The reconciliation pass in `_extract_segmented()` uses fuzzy matching, which is fragile. Additionally, each segment starts with a fresh catalog, meaning entity context from prior segments is not available during extraction — exactly the scenario described in [Extraction-Lessons.md §1.3](Extraction-Lessons.md) ("Late-Stage Text Is Not a Complete Snapshot").

---

## 4. Gap Analysis

### 4.1 Known Solutions to Problems Currently Solved with Ad-Hoc Fixes

| Current Problem | Current Fix | Established Alternative |
|----------------|------------|------------------------|
| LLM produces non-schema field names | 50+ entry coercion map in `_coerce_entity_fields()` | **Structured output / constrained decoding.** OpenAI's structured outputs, Ollama's format parameter, and vLLM's guided generation can constrain output to match a JSON schema exactly. This eliminates field-name drift at the model level rather than patching it post-hoc. Gemini's zero-coercion result with standard JSON mode demonstrates this. |
| Event extractor fabricates entity IDs | Post-hoc orphan sweep + stub creation + name-mention discovery | **Closed-vocabulary entity linking.** Provide entity IDs as a constrained enum in the prompt or response schema. The event extractor should select from known IDs, not generate new ones. Unknown entities should be flagged for a separate discovery pass. |
| Entity discovery stops finding new entities (context exhaustion) | Segmented extraction with reconciliation | **Retrieval-based entity context.** Instead of sending all entities, embed entity descriptions and retrieve the K most relevant for the current turn. Alternatively, use a tiered scheme: send recent entities in full, send a compressed roster for others. |
| Relationship strings vary per turn ("assists", "befriends", "offers companionship") | Per-pair consolidation in catalog_merger | **Relationship type ontology.** The schema already defines relationship types (kinship, partnership, etc.) but the LLM also generates a free-text `current_relationship` field. A two-tier approach — constrained type + free-text description — already exists in the schema but isn't enforced strictly enough in the template to prevent accumulation. |
| PC context grows and causes timeouts | Context trimming (`_format_prior_entity_context`), volatile digest, cooldown cycle | **Context budget enforcement.** Measure prompt token count before sending; truncate or summarize to fit within a hard budget. This is deterministic rather than heuristic. |
| Concept-prefix entity hallucinations (76 instances in Qwen run) | Post-hoc concept-prefix filter | **Negative examples in prompt.** Add 2–3 examples of things that are NOT entities (atmospheric descriptions, narrative themes) to the discovery template. This is a standard prompt engineering technique for reducing false positives. |

### 4.2 Design Gaps Without Obvious Fixes

| Gap | Description | Why It's Not Simple |
|-----|-------------|-------------------|
| **No token counting** | The pipeline has no mechanism to measure prompt size before sending. Context management is heuristic (trim volatile to 3 entries, digest entries >50 turns old). | Token counting requires a tokenizer matched to the model. The provider-agnostic design means the system doesn't know which tokenizer to use. A rough estimate (words × 1.3) would suffice for budgeting. |
| **No extraction quality signal** | The pipeline knows whether an LLM call returned valid JSON but has no signal for *extraction quality*. A response that returns zero entities is treated the same as one that returns five. | Quality measurement requires ground truth or heuristic proxies (e.g., "a DM turn with 500 words of narrative should produce at least one entity"). The validate_extraction.py tool provides post-hoc quality checking but isn't integrated into the extraction loop. |
| **Segmented reconciliation is fragile** | Entity matching across segments uses fuzzy string matching. Two segments may independently extract "Kael" as `char-young-hunter` and `char-kael`, producing a duplicate that fuzzy matching may or may not catch. | Cross-segment entity linking is fundamentally a coreference problem. A robust solution would require either carrying forward entity context across segments (defeating the purpose of segmentation) or a dedicated reconciliation LLM pass. |
| **No cost/quota awareness** | The pipeline tracks failed turns but doesn't estimate remaining API budget or pace requests to stay within quotas. | Cloud API quota structures vary by provider and tier. A general solution requires provider-specific rate limit detection, which is partially addressed by retry logic but not by proactive pacing. |

---

## 5. Structural Limits

### 5.1 Problems Fixable Within the Current Architecture

These are issues that can be addressed through targeted changes to existing code without altering the fundamental pipeline structure:

1. **Known-entity list context exhaustion (#221).** Replace full-roster forwarding in the discovery prompt with a tiered approach: full details for entities mentioned in recent turns (e.g., last 10), names-only for others, omit entities dormant for 50+ turns. This requires changes to `format_known_entities()` and the discovery template but not to the pipeline's sequential turn-processing model.

2. **Checkpoint not persisting entity data (#220).** The checkpoint saves `extraction-progress.json` but catalogs are only written at the end of the run (or per segment). Adding a `save_catalogs()` call at each checkpoint interval is a localized fix.

3. **API quota exhaustion (#211).** Adding quota-aware pacing (e.g., monitor 429 response frequency, insert delays, split runs across days) is an infrastructure improvement that doesn't change the extraction logic.

4. **End-of-run entity refresh (#212).** Already implemented per the architecture doc. This was a missing edge case, not a structural problem.

5. **PC extraction failures.** The PC's context trimming (`_format_prior_entity_context`) can be made more aggressive or token-budgeted. The fundamental issue — the PC accumulates more state than other entities — is real but addressable within the current per-entity extraction model.

6. **Coercion load.** Structured output modes (OpenAI's `response_format` with schema, Ollama's format parameter) can eliminate most field-name coercion. The coercion layer should remain as a fallback but the primary fix is at the model interaction level.

### 5.2 Problems That Suggest a Different Approach

These are issues where incremental fixes within the current architecture will yield diminishing returns:

#### A. Sequential Full-State Forwarding Does Not Scale

The pipeline's core loop sends the complete known-entity roster on every discovery call. This is the correct approach for turns 1–50 but becomes the dominant failure mode at scale. The Qwen run demonstrated total discovery collapse at turn 126. Even with a 32K-context model, a 500-entity game would exhaust the window.

**Why incremental fixes have limits:** Tiered context (send summaries for old entities) reduces the problem but doesn't eliminate it. The entity list still grows monotonically. Segmentation resets the list per segment but loses cross-segment context. The fundamental tension is between giving the discovery agent enough context to resolve coreferences and keeping the prompt within context limits.

**What a different approach looks like:** Retrieval-based entity context. Instead of sending all entities, embed entity descriptions and retrieve the top-K most semantically similar to the current turn text. This keeps the discovery prompt at a fixed size regardless of how many entities exist. The tradeoff is that retrieval may miss relevant entities that aren't textually similar to the current turn — but the existing full-state approach also misses entities once context is exhausted, just in a less controlled way.

#### B. Entity Discovery and Entity Linking Are Conflated

The discovery agent is asked to simultaneously:
1. Identify every entity mentioned in the turn
2. Determine whether each mention matches a known entity (coreference resolution)
3. Propose IDs for new entities

This is three tasks in one prompt, and they have different scaling properties. Task 1 (NER) is bounded by turn length. Task 2 (coreference) scales with the number of known entities. Task 3 (ID generation) requires understanding the ID naming conventions. When context is exhausted, task 2 fails silently — the model matches new entities to existing ones rather than proposing new entries, because matching is the lower-energy response when the known-entity list dominates the context.

**Why incremental fixes have limits:** Making the known-entity list smaller helps but doesn't resolve the conflation. A smaller list means the model has less information for coreference resolution, which increases false-positive new-entity creation (the opposite failure mode).

**What a different approach looks like:** Separate NER from linking. Run a first pass that identifies all entity mentions in the turn without reference to the known catalog (pure NER — "what names and descriptions appear in this text?"). Then run a second pass that links mentions to known entities using focused comparisons (for each mention, retrieve the 5 most similar known entities and ask the LLM to decide). This doubles the LLM calls for discovery but makes each call simpler and context-bounded.

#### C. The Pipeline Has No Feedback Loop for Extraction Quality

The pipeline processes turns forward-only. If entity discovery misses an entity in turn 50, that entity is absent from subsequent turns' known-entity lists, which means the relationship mapper and event extractor cannot reference it either. The miss compounds across all downstream agents and all subsequent turns.

The post-batch recovery passes (orphan sweep, name-mention discovery, stub backfill) partially compensate, but they run only at the end of the batch. For a 345-turn session, an entity missed at turn 50 is absent for 295 turns before recovery attempts.

**Why incremental fixes have limits:** Running recovery passes more frequently (e.g., every 50 turns) would help but adds cost and complexity. The fundamental issue is that the pipeline lacks a mechanism to detect extraction gaps during processing.

**What a different approach looks like:** A periodic "audit" pass that scans recent events for entity references not in the catalog and triggers targeted re-extraction. Alternatively, the event extractor could explicitly flag when it references entity IDs not in the known-entity list — this signal already exists implicitly (orphan IDs in events) but isn't fed back into the discovery agent during the run.

#### D. Four-Agent Sequential Processing Is Expensive for What It Produces

The four-agent pipeline makes ~6 LLM calls per turn on average for a mature catalog (1 discovery + ~3 entity details + 1 relationship + 1 event). Over 345 turns, this is ~2,000 calls. Run 12 (Gemini) consumed ~3,000 calls (including retries) at $20.25 and 11 hours.

The entity detail extractor is the most expensive agent — it runs once per entity mentioned in the turn, and many of these calls produce minimal updates (the entity appeared in the turn but nothing changed). The call volume analysis in [semantic-extraction-design.md §4.5](semantic-extraction-design.md) anticipated this but proposed no mitigation.

**Why incremental fixes have limits:** Skipping entity detail calls when `is_new: false` and `significant_update: false` (already partially implemented) reduces calls but requires the discovery agent to accurately assess significance — an additional judgment that small models handle poorly.

**What a different approach looks like:** Batch entity updates. Instead of one LLM call per entity per turn, send the turn text with a list of mentioned entities and ask for updates to all of them in a single call. This reduces call count from ~4 per turn to ~2 (discovery + batch update for entities and events combined). The tradeoff is that per-entity attention decreases, which may reduce extraction quality for complex entities. A hybrid approach — batch updates for straightforward turns, per-entity calls for turns with significant narrative events — could balance cost and quality.

---

## 6. Alternative Approaches

If designing this system from scratch with the same goals (structured world model from long narrative text, provider-agnostic, file-based storage), these approaches merit consideration:

### Approach A: Retrieval-Augmented Sequential Extraction

**Core idea:** Replace full-state forwarding with retrieval-based context selection. Embed entity descriptions in a lightweight vector store (e.g., using sentence-transformers locally or the LLM's own embeddings). For each turn, retrieve the K most relevant entities to include in the discovery prompt.

**Architecture:**
```
Turn text → embed → retrieve top-K entities from vector store
         → discovery (turn + K entities) → detail + relationship + events
         → update vector store with new/modified entities
         → merge into catalogs
```

**Tradeoffs:**
- (+) Context is bounded regardless of catalog size
- (+) Discovery sees the most relevant entities for coreference resolution
- (+) Works with small-context models
- (−) Requires an embedding model (additional dependency)
- (−) Retrieval may miss relevant entities not textually similar to the turn
- (−) Vector store adds infrastructure (though lightweight local options exist: FAISS, ChromaDB)
- (−) Embedding quality for fantasy names is uncertain

**Scale:** Works from 10 to 10,000 entities. Context budget is fixed.

### Approach B: Two-Pass Extraction (NER → Linking → Detail)

**Core idea:** Separate entity recognition from entity linking. First pass identifies mentions; second pass links them to known entities; third pass extracts details.

**Architecture:**
```
Turn text → Pass 1: NER (no catalog context)
         → Pass 2: Entity linking (mention + top-5 candidates per mention)
         → Pass 3: Detail extraction (linked entities only)
         → Pass 4: Events + relationships
```

**Tradeoffs:**
- (+) Each pass has focused context; no unbounded growth
- (+) NER quality doesn't degrade with catalog size
- (+) Entity linking can use targeted comparison rather than full-roster scan
- (−) More LLM calls per turn (adds linking pass)
- (−) Requires entity matching infrastructure (even if simple TF-IDF similarity)
- (−) Two-pass latency per turn

**Scale:** Linear in turn count; entity linking scales with K (candidates per mention), not total entities.

### Approach C: Chunk-and-Consolidate (Map-Reduce Style)

**Core idea:** Process the transcript in independent chunks (e.g., 25-turn segments), then run a consolidation pass that merges entity catalogs across chunks.

**Architecture:**
```
Transcript → chunk into segments (25-50 turns each)
           → parallel extraction per segment (fresh catalog each)
           → consolidation pass: cross-segment entity dedup + relationship merge
           → final catalog
```

This is essentially the existing `--segment-size` approach, formalized.

**Tradeoffs:**
- (+) Parallelizable — segments can run concurrently
- (+) Fresh context per segment avoids growth problems
- (+) Segment failures don't cascade
- (−) Cross-segment entity resolution is hard (the reconciliation problem)
- (−) Late segments lack context from early segments (the "late text is not a snapshot" problem)
- (−) Consolidation pass requires its own LLM calls or sophisticated heuristics
- (−) Entity relationships that span segments are harder to detect

**Scale:** Parallelism bounded by API rate limits; consolidation pass is O(entities² × segments).

### Approach D: Single-Pass Large-Context Extraction

**Core idea:** Use a large-context model (100K+) to process the entire transcript or large sections in a single pass, extracting a complete entity catalog at once.

**Architecture:**
```
Full transcript (or 100-turn section) → single LLM call: extract all entities
                                      → single LLM call: extract all relationships
                                      → single LLM call: extract all events
                                      → validate and persist
```

**Tradeoffs:**
- (+) Simplest architecture; no state management, no incremental merging
- (+) LLM sees full context; best possible coreference resolution
- (+) Minimal LLM call count (3-5 per section)
- (−) Requires large-context model (100K+ tokens)
- (−) Single-call extraction quality for 50+ entities is uncertain
- (−) No incremental processing; full re-extraction needed for new turns
- (−) Expensive per call; cost scales with transcript length
- (−) Output size may exceed model's output token limit
- (−) Entity attention may be shallow compared to per-entity focused extraction

**Scale:** Bounded by model context window. A 345-turn transcript at ~300 tokens/turn is ~100K tokens — feasible for Gemini, Claude, GPT-4o but not for most local models.

### Approach E: Hybrid — Incremental Extraction with Periodic Full-Context Reconciliation

**Core idea:** Combine the current turn-by-turn pipeline (for incremental extraction) with periodic full-context passes (for reconciliation and quality recovery).

**Architecture:**
```
Normal turns: lightweight turn-by-turn extraction (current pipeline with bounded context)
Every N turns: full-context reconciliation pass
  → send catalog + last N turns to large-context model
  → ask: "Are there entities in these turns not in the catalog? Are any catalog entries duplicates?"
  → merge corrections into catalog
End of run: final reconciliation with full transcript summary
```

**Tradeoffs:**
- (+) Maintains incremental processing benefits (fast per-turn)
- (+) Periodic reconciliation catches gaps without waiting for end-of-batch
- (+) Can use different models for different passes (cheap model for routine turns, expensive model for reconciliation)
- (−) Two-model complexity; reconciliation prompts need design
- (−) Reconciliation pass may produce conflicting updates
- (−) Cost increases with reconciliation frequency

**Scale:** Incremental pass scales linearly; reconciliation cost is O(N × catalog size).

### Approach F: Event-First Extraction

**Core idea:** Invert the pipeline. Extract events first (which requires no prior state), then derive entities from event participants.

**Architecture:**
```
Turn text → Event extraction (no catalog context needed)
         → Entity roster derived from event related_entities
         → Entity detail extraction for new/changed entities
         → Relationship inference from co-occurrence in events
```

**Tradeoffs:**
- (+) Event extraction doesn't require a known-entity list
- (+) Entity discovery happens organically from event participants
- (+) Aligns with the "state machine, not story" principle from [Extraction-Lessons.md](Extraction-Lessons.md)
- (−) Event extraction without entity context may produce inconsistent IDs (the orphan ID problem already seen in Run 12)
- (−) Entities not involved in events are missed (background characters, locations described but not acted in)
- (−) Relationship quality depends on event granularity

**Scale:** Event extraction is bounded by turn text only; entity derivation scales with event count.

---

## 7. Recommendations

### Keep As-Is

1. **Four-agent specialization.** The separation of discovery, detail, relationships, and events is sound and should be preserved. Collapsing them into fewer agents would reduce extraction quality.

2. **Schema-first validation.** The 17-schema validation layer is a genuine strength. Any redesign should maintain this.

3. **V2 catalog structure.** The per-entity files, index files, stable/volatile split, and identity/current_status separation are well-designed. These should remain the target data model regardless of how extraction works.

4. **Provider-agnostic LLM client.** The ability to switch between Ollama, OpenAI, and Gemini with a config change is valuable and should be preserved.

5. **Provenance tracking.** `first_seen_turn`, `last_updated_turn`, and `source_turns` are essential for this domain. Do not sacrifice provenance for simpler schemas.

6. **Post-batch recovery passes.** The orphan sweep, name-mention discovery, and dedup passes are pragmatic and effective. They should continue as safety nets even if upstream extraction improves.

### Reconsider

1. **Entity context management in the discovery prompt.** This is the single highest-impact design change available. The current full-roster forwarding is the root cause of the most severe failure mode (zero discovery after turn 126 on Qwen). A tiered or retrieval-based approach to entity context selection would make the pipeline viable across a wider range of models and session lengths.

   Concrete starting point: implement a token budget for the known-entity portion of the discovery prompt. When the entity list exceeds the budget, include recently-active entities in full and reduce others to name-only or omit dormant entities. This requires no new infrastructure (no vector store) and can be implemented as a change to `format_known_entities()`.

2. **Separation of NER from entity linking.** The current discovery agent conflates mention detection with coreference resolution. Under context pressure, coreference consumes the budget at the expense of new-entity detection. Separating these — even as two sections within a single prompt rather than two separate calls — would make failure modes more transparent.

3. **Token counting and context budgeting.** The pipeline makes zero measurements of prompt size before sending. Adding even a rough token estimate (word count × 1.3) would enable adaptive context management: trim the entity list, skip relationship context, or fall back to a simpler prompt when approaching limits. This is a prerequisite for reliable operation across model sizes.

4. **Inline quality signals.** The pipeline should detect likely extraction gaps during processing, not only at the end. The simplest signal: when the event extractor produces entity IDs not in the known-entity list, those IDs should be fed back to trigger a targeted discovery pass on the same turn. This closes the feedback loop between event extraction and entity discovery without waiting for the post-batch orphan sweep.

5. **Cost and quota management.** For cloud providers, the pipeline should estimate total API calls before starting and warn if the expected cost or call count approaches quota limits. For rate-limited providers, it should pace requests based on observed 429 response frequency rather than using a fixed delay.

### What Requires Further Investigation

1. **Retrieval-augmented entity context.** This is the most promising approach for eliminating context growth, but its effectiveness for fantasy-name coreference is unknown. A small experiment — embedding 50 entity descriptions with a local embedding model and measuring retrieval recall against ground truth — would determine whether this approach is viable before committing to implementation.

2. **Structured output effectiveness across providers.** Gemini's zero-coercion result suggests that the coercion layer may be largely unnecessary with models that support structured output well. Testing structured output modes on Ollama (which supports a `format` parameter for JSON schemas) would determine whether the coercion layer can be reduced for local models too.

3. **Batch entity updates.** Combining entity detail extraction for multiple entities into a single LLM call could reduce call volume by 50–70%. The quality impact is unknown and would need testing — specifically, whether a single call processing 5 entities produces comparable results to 5 individual calls.

4. **Event-first extraction viability.** The event-first approach (Approach F) aligns with the project's stated principle that narratives are state machines, not stories. However, the Run 12 evidence shows that event extraction without entity context produces inconsistent IDs. A hybrid test — events first, then entity reconciliation — would clarify whether this inversion is practical.

---

## Appendix: Evidence Index

| Claim | Source |
|-------|--------|
| Qwen: zero entities on disk after 24h run | [test-report-run12.md](../framework-local/test-report-run12.md) |
| Qwen: zero new entities after turn 126 | [test-report-run12.md](../framework-local/test-report-run12.md), entity discovery counts by turn range |
| Qwen: 100% PC validation failure (62 attempts) | [test-report-run12.md](../framework-local/test-report-run12.md) |
| Qwen: 38 unique coercion types | [test-report-run12.md](../framework-local/test-report-run12.md) |
| Qwen: 76 concept-prefix hallucinations | [test-report-run12.md](../framework-local/test-report-run12.md) |
| Qwen: context exhaustion math (8K window) | System prompt ~800 + entities ~7,700 + turn ~500 > 8,192 |
| Gemini: 210 entities, 404 events, zero parse failures | [test-report-run12-gemini.md](../framework-local/test-report-run12-gemini.md) |
| Gemini: turns 302–344 zero output (quota exhaustion) | [test-report-run12-gemini.md](../framework-local/test-report-run12-gemini.md) |
| Gemini: 5 named characters never extracted | [test-report-run12-gemini.md](../framework-local/test-report-run12-gemini.md) (Rune, Gorok, Chief Thorne, Elder Lyra, Maelis) |
| Gemini: 10 false PC aliases | [test-report-run12-gemini.md](../framework-local/test-report-run12-gemini.md) |
| Gemini: 25 stale entities at run end | [test-report-run12-gemini.md](../framework-local/test-report-run12-gemini.md) |
| Gemini: $20.25 total cost | [test-report-run12-gemini.md](../framework-local/test-report-run12-gemini.md) |
| char-player 37 relationships / 14 synonyms | [design-catalog-v2.md](design-catalog-v2.md) §2 |
| char-player → char-young-hunter 14 separate entries | [design-catalog-v2.md](design-catalog-v2.md) §5 |
| 50+ field-name coercion remaps | [semantic_extraction.py](../tools/semantic_extraction.py) lines 340–430 |
| Four-agent pipeline design | [semantic-extraction-design.md](semantic-extraction-design.md) §4 |
| Known-entity format: ID \| name \| type \| identity | [semantic_extraction.py](../tools/semantic_extraction.py) `format_known_entities()` |
| Entity discovery stops finding entities (issue #106) | [design-entity-pipeline-v3.md](design-entity-pipeline-v3.md) |
| Event extractor fabricates IDs (34 orphans / 53% of events) | [design-entity-pipeline-v3.md](design-entity-pipeline-v3.md), issue #108 |
| Narrative is a state machine, not a story | [Extraction-Lessons.md](Extraction-Lessons.md) §1.1 |
| Late-stage text is not a complete snapshot | [Extraction-Lessons.md](Extraction-Lessons.md) §1.3 |
| Entity persistence is non-negotiable | [Extraction-Lessons.md](Extraction-Lessons.md) §1.2 |
| Segmented reconciliation: fuzzy matching | [semantic_extraction.py](../tools/semantic_extraction.py) `_extract_segmented()`, lines 2222–2280 |
| PC context trimming: volatile digest, relationship arcs | [semantic_extraction.py](../tools/semantic_extraction.py) `_format_prior_entity_context()`, lines 926–975 |
| Dedup: 4-phase fuzzy matching | [semantic_extraction.py](../tools/semantic_extraction.py) `_dedup_catalogs()`, lines 1835–2020 |
