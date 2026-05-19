# A/B Test Design: Context Optimization Experiments

## Goal

Measure the impact of three context optimization ideas on extraction quality and performance, using turns 200–225 as a standardized test range.

---

## Variants

| ID | Name | Description |
|----|------|-------------|
| `baseline` | Current code | No modifications; current prompt-building logic |
| `rel-score` | Relationship Relevance Scoring | Filter relationships passed to relationship-mapper by mention recency, co-location, and interaction frequency |
| `arc-compress` | Arc-Aware Compression | Extend PC relationship history compression (arcs) to ALL entities, not just char-player |
| `scene-scope` | Scene-Scoped Entity Detail | Trim non-PC entity catalog entries in the detail prompt based on scene relevance (entities not in current scene get minimal context) |
| `combined` | All three combined | rel-score + arc-compress + scene-scope |

---

## Dataset

- **Turn range**: 200–225 (26 DM turns)
- **Why this range**: Late enough for large catalogs (154 entities, 45 locations, 69 characters at turn 199), non-trivial relationship density, representative of real extraction load
- **Baseline catalogs**: Snapshot from `framework-local/ab-test/qwen35-pipelined/` at turn 199

## Catalog Snapshot Strategy

Each variant run starts from the **same frozen catalog state** at turn 199. This ensures differences in output are due to the variant, not divergent catalog drift.

```
framework-local/ab-test/qwen35-pipelined/catalogs/   ← source of truth (turn 199)
framework-local/ab-test/ctx-baseline/catalogs/        ← copy for baseline run
framework-local/ab-test/ctx-rel-score/catalogs/       ← copy for rel-score run
framework-local/ab-test/ctx-arc-compress/catalogs/    ← copy for arc-compress run
framework-local/ab-test/ctx-scene-scope/catalogs/     ← copy for scene-scope run
framework-local/ab-test/ctx-combined/catalogs/        ← copy for combined run
```

---

## Metrics

### Per-Turn Metrics (captured in extraction-log.jsonl + extended metrics file)

| Metric | Source | Notes |
|--------|--------|-------|
| `elapsed_ms` | extraction-log.jsonl | Total turn time (already captured) |
| `discovery_ms` | extraction-log.jsonl | Discovery phase time (already captured) |
| `parallel_ms` | extraction-log.jsonl | Detail+relationship+event phase time (already captured) |
| `temporal_ms` | extraction-log.jsonl | Temporal extraction phase time (already captured) |
| `new_entities` | extraction-log.jsonl | New entities discovered (already captured) |
| `new_events` | extraction-log.jsonl | New events extracted (already captured) |
| `discovery_proposals` | extraction-log.jsonl | Raw discovery output (already captured) |
| `relationship_prompt_tokens` | **NEW** — extended log | Token count of relationship-mapper user prompt |
| `entity_detail_prompt_tokens_avg` | **NEW** — extended log | Average token count of entity-detail user prompts for the turn |
| `entity_detail_prompt_tokens_max` | **NEW** — extended log | Max token count across entity-detail prompts |
| `discovery_prompt_tokens` | **NEW** — extended log | Token count of discovery user prompt |
| `llm_calls` | **NEW** — extended log | Total LLM API calls for this turn |
| `relationships_extracted` | **NEW** — extended log | Count of relationships returned |
| `json_valid` | **NEW** — extended log | Whether all phase outputs validated against schema |

### Aggregate Metrics (computed in comparison report)

| Metric | Computation |
|--------|-------------|
| Mean elapsed per turn | avg(elapsed_ms) across turns 200–225 |
| Total prompt tokens saved | sum(baseline prompt tokens) − sum(variant prompt tokens) |
| Entity discovery parity | count of entities discovered by variant vs baseline |
| Relationship parity | count of relationships extracted by variant vs baseline |
| LLM call count parity | total calls by variant vs baseline |
| Error rate | count of phase failures / total phases |

---

## Configuration Mechanism

### Option: `context_optimizations` dict in `config/llm.json`

Add a new top-level key to `config/llm.json`:

```json
{
  "context_optimizations": {
    "relationship_relevance_scoring": false,
    "arc_compress_all_entities": false,
    "scene_scoped_entity_detail": false
  }
}
```

Each optimization reads its flag from `llm.config` (already accessible via `getattr(llm, "config", {})` throughout `semantic_extraction.py`). When absent, all flags default to `false` (baseline behavior).

This approach:
- Requires zero new files or CLI flags
- Uses the existing config propagation path
- Allows per-variant config files (e.g., `config/llm-rel-score.json`)
- Is consistent with existing config-driven behavior (checkpoint_interval, dedup_audit_interval, etc.)

### Per-Variant Config Files

```
config/llm.json                          ← baseline (no context_optimizations)
config/llm-rel-score.json                ← {"context_optimizations": {"relationship_relevance_scoring": true}}
config/llm-arc-compress.json             ← {"context_optimizations": {"arc_compress_all_entities": true}}
config/llm-scene-scope.json              ← {"context_optimizations": {"scene_scoped_entity_detail": true}}
config/llm-combined.json                 ← all three true
```

Each variant config file is a full copy of `llm.json` with only the `context_optimizations` block changed. The `--config` flag on `bootstrap_session.py` (passed through to `LLMClient`) selects the variant.

---

## Test Runner: `tools/ab_test_runner.py`

### Purpose

Automates A/B test runs: copies the frozen catalog snapshot, runs extraction on the target turn range with a specific config, captures extended metrics, and saves structured results.

### CLI Interface

```
python tools/ab_test_runner.py \
  --variant baseline \
  --start-turn 200 \
  --end-turn 225 \
  --source-catalogs framework-local/ab-test/qwen35-pipelined/catalogs \
  --output-dir framework-local/ab-test/ctx-baseline \
  --config config/llm.json \
  --session sessions/avernus \
  --runs 1
```

### What It Does

1. **Setup**: Copy `--source-catalogs` → `--output-dir/catalogs/` (fresh each run)
2. **Run**: Invoke `run_semantic_extraction()` with `--start-turn` / `--max-turns` targeting turns 200–225, writing to `--output-dir`
3. **Capture extended metrics**: Instrument `extract_and_merge()` to emit additional fields (prompt token counts, LLM call counts, relationship counts)
4. **Save results**: Write `--output-dir/ab-results.json` with per-turn metrics array + aggregate summary

### Extended Metric Instrumentation

The cleanest approach: add optional metrics collection to `extract_and_merge()` via a callback or accumulator dict.

```python
# In extract_and_merge(), after building each prompt:
if metrics_collector is not None:
    metrics_collector["relationship_prompt_tokens"] = _estimate_tokens(rel_user_prompt)
    metrics_collector["entity_detail_prompt_tokens"].append(_estimate_tokens(detail_user_prompt))
    metrics_collector["llm_calls"] += 1
```

The `_log_record` already built by `extract_and_merge()` (line 3083) is extended with these fields when the collector is provided. No change to the log format when the collector is absent.

### Implementation Phases

**Phase 1 — Metrics instrumentation** (required for all variants):
- Add `_estimate_tokens` calls at prompt-building sites in `extract_and_merge()`
- Extend `_log_record` with new fields (backward-compatible: new fields only appear when present)
- Add `llm_call_count` tracking to `extract_and_merge()`

**Phase 2 — Test runner script** (`tools/ab_test_runner.py`):
- Catalog snapshot copy
- Config selection
- Invocation of extraction
- Results aggregation

**Phase 3 — Comparison script** (`tools/ab_test_compare.py`):
- Load results from multiple variant directories
- Compute deltas and parity metrics
- Output a markdown comparison table

**Phase 4 — Implement the three variants** (separate PRs):
- Each variant modifies prompt-building functions gated by `context_optimizations` flags
- Each PR includes its own focused test

---

## Exact Commands to Run on Arclight

### Prerequisites

```bash
# SSH to arclight
ssh arclight

# Activate environment
cd ~/narrative-state-engine
source .venv/bin/activate

# Ensure LLM server is running
curl -s http://localhost:8000/v1/models | python -m json.tool

# Verify baseline catalogs exist at turn 199
python -c "
import json, os
d = 'framework-local/ab-test/qwen35-pipelined/catalogs'
for sub in ['characters','factions','items','locations']:
    p = os.path.join(d, sub)
    n = len([f for f in os.listdir(p) if f.endswith('.json') and not f.endswith('.arcs.json')])
    print(f'{sub}: {n}')
"
```

### Run Baseline

```bash
python tools/ab_test_runner.py \
  --variant baseline \
  --start-turn 200 --end-turn 225 \
  --source-catalogs framework-local/ab-test/qwen35-pipelined/catalogs \
  --output-dir framework-local/ab-test/ctx-baseline \
  --config config/llm.json \
  --session sessions/avernus \
  2>&1 | tee framework-local/ab-test/ctx-baseline.log
```

### Run Variants

```bash
# Relationship Relevance Scoring
python tools/ab_test_runner.py \
  --variant rel-score \
  --start-turn 200 --end-turn 225 \
  --source-catalogs framework-local/ab-test/qwen35-pipelined/catalogs \
  --output-dir framework-local/ab-test/ctx-rel-score \
  --config config/llm-rel-score.json \
  --session sessions/avernus \
  2>&1 | tee framework-local/ab-test/ctx-rel-score.log

# Arc-Aware Compression
python tools/ab_test_runner.py \
  --variant arc-compress \
  --start-turn 200 --end-turn 225 \
  --source-catalogs framework-local/ab-test/qwen35-pipelined/catalogs \
  --output-dir framework-local/ab-test/ctx-arc-compress \
  --config config/llm-arc-compress.json \
  --session sessions/avernus \
  2>&1 | tee framework-local/ab-test/ctx-arc-compress.log

# Scene-Scoped Entity Detail
python tools/ab_test_runner.py \
  --variant scene-scope \
  --start-turn 200 --end-turn 225 \
  --source-catalogs framework-local/ab-test/qwen35-pipelined/catalogs \
  --output-dir framework-local/ab-test/ctx-scene-scope \
  --config config/llm-scene-scope.json \
  --session sessions/avernus \
  2>&1 | tee framework-local/ab-test/ctx-scene-scope.log

# Combined
python tools/ab_test_runner.py \
  --variant combined \
  --start-turn 200 --end-turn 225 \
  --source-catalogs framework-local/ab-test/qwen35-pipelined/catalogs \
  --output-dir framework-local/ab-test/ctx-combined \
  --config config/llm-combined.json \
  --session sessions/avernus \
  2>&1 | tee framework-local/ab-test/ctx-combined.log
```

### Compare Results

```bash
python tools/ab_test_compare.py \
  --baseline framework-local/ab-test/ctx-baseline \
  --variants framework-local/ab-test/ctx-rel-score \
             framework-local/ab-test/ctx-arc-compress \
             framework-local/ab-test/ctx-scene-scope \
             framework-local/ab-test/ctx-combined \
  --output framework-local/ab-test/comparison-report.md
```

---

## Statistical Considerations

### Timing Metrics
- With 26 turns per variant and temperature > 0, there will be natural variance
- For timing: report mean ± stddev; a >10% change in mean elapsed_ms is meaningful
- For production confidence: run each variant 3× (78 turns total per variant) and compare distributions
- Single-run is sufficient for initial investigation; multi-run for final decision

### Quality Metrics
- Entity/relationship counts: exact comparison (should not drop)
- Schema validation: must be 100% for all variants (quality gate)
- A variant that reduces prompt tokens but loses entities is rejected

### Pass/Fail Criteria

| Metric | Pass condition |
|--------|---------------|
| Entities extracted | ≥ 95% of baseline count per turn |
| Relationships extracted | ≥ 90% of baseline count per turn |
| Schema validation | 100% |
| Mean elapsed_ms | No increase > 5% (context prep time should decrease) |
| Prompt tokens | Measurable decrease (the whole point) |

---

## What Exists vs. What's New

### Existing Infrastructure (reusable)

| Component | Location | Reuse |
|-----------|----------|-------|
| `bootstrap_session.py` | `tools/` | `--start-turn` / `--max-turns` for turn range; `--framework` for output dir; `--config` for variant config |
| `extract_and_merge()` | `tools/semantic_extraction.py` | Core extraction loop with per-turn log records |
| `_write_extraction_log()` | `tools/semantic_extraction.py` | JSONL log writer (append-only) |
| `_estimate_tokens()` | `tools/catalog_merger.py` | Cheap token estimation (len/3) |
| `LLMClient` | `tools/llm_client.py` | Config-driven, supports `--config` path |
| `discovery_baseline.py` | `tools/` | Pattern for measurement harness (load catalogs, call LLM, capture metrics) |
| `extraction-log.jsonl` | Written by extraction | Already has elapsed_ms, discovery_ms, parallel_ms, phase success/failure |
| Config propagation | `llm.config` accessible in extraction | New flags just need new keys in the dict |

### New Code Required

| Component | Est. Size | Description |
|-----------|-----------|-------------|
| `tools/ab_test_runner.py` | ~150 lines | Orchestrator: copy catalogs, run extraction, save results |
| `tools/ab_test_compare.py` | ~100 lines | Load results from variants, compute deltas, generate markdown report |
| Extended metrics in `extract_and_merge()` | ~30 lines | Add `_estimate_tokens` at prompt-build sites, count LLM calls, count relationships |
| `context_optimizations` config reading | ~10 lines | Read flags from `llm.config` in prompt-building functions |
| Per-variant `config/llm-*.json` files | ~5 files | Copies of llm.json with optimization flags |
| Variant implementations | ~50–150 lines each | The actual optimization code (separate PRs) |

### Implementation Order

1. **Extended metrics** — Instrument `extract_and_merge()` with prompt token counts and LLM call tracking (small, backward-compatible change to `semantic_extraction.py`)
2. **Config mechanism** — Add `context_optimizations` reading pattern (tiny, no behavior change)
3. **Test runner** — `tools/ab_test_runner.py` (new file, no existing code changes)
4. **Comparison tool** — `tools/ab_test_compare.py` (new file)
5. **Run baseline** — Validate the harness produces good data
6. **Implement variants** — One PR per variant, gated by config flags
7. **Run variants + compare** — Execute the full A/B test matrix

---

## Alternative: Direct bootstrap_session.py Invocation

If we want to skip the custom runner and use existing infrastructure directly:

```bash
# Copy catalogs
cp -r framework-local/ab-test/qwen35-pipelined/catalogs framework-local/ab-test/ctx-baseline/catalogs

# Run extraction directly
python tools/bootstrap_session.py \
  --session sessions/avernus \
  --file sessions/avernus/raw/full-transcript.md \
  --framework framework-local/ab-test/ctx-baseline \
  --start-turn 200 --max-turns 225 \
  --segment-size 0 \
  --skip-backfill
```

This works today without any new code, but misses the extended metrics (prompt token counts, LLM call counts). The `ab_test_runner.py` wrapper adds those while delegating to the same extraction functions.
