# A/B Testing Standard for Template Changes

This document defines the required A/B testing gate for any PR that modifies extraction prompt templates (`templates/extraction/*.md`). It was introduced after PR #393 (smart compression) caused 27% entity loss that was not detected until manual testing.

**Scope:** Any PR that adds, removes, or modifies files under `templates/extraction/` MUST include an A/B test report posted as a PR comment before merging.

---

## 1. Test Scope

### 1.1 Turn Selection

| Parameter | Minimum | Recommended |
|---|---|---|
| Ground truth range | Turns 1–30 | Turns 1–30 |
| Extended range | — | Turns 1–50 |
| Full session | — | Turns 1–345 (for major changes) |

- **Turns 1–30** are always required. Ground truth fixtures exist at `tests/fixtures/extraction-ground-truth-turns-1-30.json` and provide entity-level validation.
- **Turns 1–50** are recommended for changes affecting relationship or event extraction.
- **Full session** (turns 1–345) should be used for structural changes to entity-discovery or entity-detail templates. Use `tests/fixtures/extraction-ground-truth-full-session.json` for validation.

### 1.2 Runs Per Variant

**The backend is not deterministic at any temperature, so a minimum of 3 runs per variant is always required.** The determinism baseline `eval-main-determinism-temp0` compared the *same* `main` commit (SHA `769e1ad`) against itself — identical templates, model (`Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`), hardware (B70), and temperature 0 — and produced divergent catalogs (per-type removed=3, added=1, renamed=2 over turns 1–10; catalog byte-diff non-zero). Temperature 0 is therefore **not** a substitute for repetition: the llama.cpp/GGUF execution path introduces run-to-run variance that a single run cannot distinguish from a template effect.

| Sampling mode | Minimum runs per variant | Recommended runs per variant |
|---|---|---|
| **Temperature 0** | 3 | 5 |
| Temperature > 0 | 3 | 5 |

Report **mean ± standard deviation** for all metrics at every temperature. If any metric's standard deviation exceeds 15% of its mean, increase to 5 runs. With 3 runs per variant, the three pairwise A-vs-A comparisons (run1↔run2, run1↔run3, run2↔run3) are used to establish the backend noise floor (§1.4) that the retention gate (§3.5) is measured against.

> **Rationale:** The earlier "temperature 0 ⇒ 1 run is sufficient" policy assumed byte-level reproducibility that the backend does not provide. Because an A-vs-A self-comparison already shows non-zero entity churn, a single B run cannot be attributed to the template change rather than to backend noise. Minimum 3 runs is chosen as the smallest count that yields multiple independent A-vs-A pairwise comparisons (Rule 10: chosen, not inherited).

### 1.3 Variant Definitions

- **Variant A (baseline):** Templates from `main` branch HEAD. Use `--framework framework-ab-a-runN` output directory (one per run, e.g. `framework-ab-a-run1`, `framework-ab-a-run2`, etc.).
- **Variant B (candidate):** Templates from the PR branch. Use `--framework framework-ab-b-runN` output directory (one per run, e.g. `framework-ab-b-run1`, `framework-ab-b-run2`, etc.).
- Both variants MUST use identical `config/llm.json`, identical model, and identical hardware.

### 1.4 Noise-Floor (A-vs-A) Baseline — REQUIRED

Before judging B-vs-A, the tester MUST establish the backend **noise floor** for this model + hardware by self-comparing the variant-A runs against each other. This quantifies the run-to-run churn caused by backend non-determinism, which becomes the tolerance band for the retention gate (§3.5).

**Procedure:**

1. Run variant A at least 3 times (`framework-ab-a-run1..3`), per §1.2.
2. Run `tools/entity_retention_diff.py` (with default `--match-by auto`, so ID-scheme renames are excluded) on **every pairwise combination** of the A runs:

   ```bash
   python tools/entity_retention_diff.py -a framework-ab-a-run1 -b framework-ab-a-run2 --json
   python tools/entity_retention_diff.py -a framework-ab-a-run1 -b framework-ab-a-run3 --json
   python tools/entity_retention_diff.py -a framework-ab-a-run2 -b framework-ab-a-run3 --json
   ```

3. For each entity type (characters, locations, items, factions, events), record the **removed**, **added**, and **renamed** counts from each pairwise diff, and the **% removed** = removed ÷ (A-side entity count of that type).
4. Define the **per-type noise floor** `NF_type` = the **maximum** removed count for that type observed across the pairwise A-vs-A comparisons (worst-case backend churn). Record the matching **% noise floor** as well. Compute an aggregate **total** noise floor `NF_total` the same way.

**Reported as a table** (include in the PR comment, §5.1):

```
| Type       | A-vs-A removed (max) | A-side count | % noise floor | NF_type |
|------------|----------------------|--------------|---------------|---------|
| characters | 3                    | 18           | 16.7%         | 3       |
| locations  | 1                    | 8            | 12.5%         | 1       |
| ...        | ...                  | ...          | ...           | ...     |
| **Total**  | 5                    | 60           | 8.3%          | 5       |
```

Renames are **never** counted toward the noise floor (they reflect ID-scheme churn, not entity loss). If `NF_type` is 0 for a type, the backend was stable for that type in this run set and the strict zero-removal expectation applies to it (subject to the +1 quantization margin in §3.5).

---

## 2. Performance Metrics

### 2.1 Required Metrics

| Metric | Source | Unit |
|---|---|---|
| Wall-clock time per turn | `elapsed_ms / 1000` from extraction log (per-turn field logged by the extraction pipeline) | seconds |
| Total extraction time | Start-to-finish wall clock | minutes |
| LLM calls per turn | Sum of `prompt_metrics.<phase>.calls` across phases recorded in `extraction-log.jsonl` for each turn: `discovery`, `entity_detail`, `relationship_mapper`, `event_extractor`. PC detail is processed as an additional `entity_detail` call (there is no distinct `pc-extraction` phase counter); it is included in the `entity_detail` call count automatically. Note: `temporal_signals` extraction calls are **not** recorded in `prompt_metrics` and are excluded from this count; if your change affects temporal extraction, track those calls separately via the extraction log's `temporal_ms` / `new_temporal_signals` fields and the timeline file. | count |

### 2.2 Derived Metrics

| Metric | Formula |
|---|---|
| Mean time per turn | total_time / turn_count |
| Throughput | turns / minute |
| Time delta vs baseline | (B_mean - A_mean) / A_mean × 100% |

### 2.3 Performance Regression Thresholds

| Threshold | Action |
|---|---|
| ≤ +10% wall-clock time | PASS — no performance concern |
| +10% to +20% wall-clock time | WARN — acceptable if quality improves; document justification |
| > +20% wall-clock time | BLOCK — must optimize before merge |
| Any performance improvement | Always PASS — no upper bound on speed gains |

### 2.4 Compression PRs — Combined Cost+Quality Gate (#464)

Any PR that changes context compression behavior (the adaptive stage, the discovery floor, or any of the `format_known_entities_bounded` / `_collect_existing_relationships` / `_trim_entry_for_scene` surfaces) MUST report cost and quality **together** and **bucketed by turn band**. Session totals are insufficient — PR #393/#394/#463 each regressed late-turn behavior that a whole-session average masked.

- **Turn-band bucketing is mandatory.** Report prompt-token cost (raw, compressed, ratio) and entity/relationship retention separately for the **1-20, 21-50, 51-100** bands (and 101+ when the run extends that far). Use `tools/agg_compression.py <extraction-log.jsonl>` to produce the per-band table for each variant.
- **Combined gate (both must hold):**
  - **Cost:** the candidate's absolute compressed token count **and** `compression_ratio` must each not *increase* in any band versus baseline — ratio alone is insufficient because a lower ratio on a larger raw context can still send more tokens; late-turn bands are the ones that matter.
  - **Quality:** zero understanding loss — `compression.dropped_then_referenced[]` must be empty, the discovery floor must hold (`floor_held=yes`), and the §3.4 Entity Retention Diff must show no net entity/relationship deletions attributable to compression in any band.
- A cost win in early bands that comes with *any* quality regression in a late band is a **BLOCK**, not a trade-off.

---

## 3. Quality Metrics — Quantitative

### 3.1 Entity Counts

Count entities by type in each extraction output:

| Type | Catalog directory | Count method |
|---|---|---|
| Characters | `catalogs/characters/` | File count (excluding `char-player.json`, `index.json`, `*.arcs.json`, `*.synthesis.json`). **Note:** `creature`-type entities are stored here too (IDs prefixed `creature-`); their files are included in this count. |
| Locations | `catalogs/locations/` | File count (excluding `index.json`, `*.arcs.json`, `*.synthesis.json`) |
| Items | `catalogs/items/` | File count (excluding `index.json`, `*.arcs.json`, `*.synthesis.json`). **Note:** `concept`-type entities are stored here too (IDs prefixed `concept-`); their files are included in this count. |
| Factions | `catalogs/factions/` | File count (excluding `index.json`, `*.arcs.json`, `*.synthesis.json`) |
| Events | `catalogs/events.json` | Array length (`jq length` or `python -c "import json; print(len(json.load(open(..., encoding='utf-8-sig'))))"`) |

Report as a table:

```
| Type       | A mean ± σ | B mean ± σ | Δ%     | Status |
|------------|-----------|-----------|--------|--------|
| Characters | 12.0 ± 0.0 | 11.3 ± 0.6 | -5.8% | WARN   |
| Locations  | 8.3 ± 0.6 | 8.7 ± 0.6 | +4.8%  | PASS   |
| ...        | ...       | ...       | ...    | ...    |
```

### 3.2 Relationship Counts

Count total relationships across all entity files. Report by relationship `type` if available using the canonical `type` enum values from `schemas/entity.schema.json`: `kinship`, `partnership`, `mentorship`, `political`, `factional`, `social`, `adversarial`, `romantic`, `spatial`, `other`.

### 3.3 JSON Schema Validity

Run `python tools/validate.py --framework <dir>` on each extraction output. For example:

```bash
python tools/validate.py --framework framework-ab-a-run1
python tools/validate.py --framework framework-ab-b-run1
```

Report:

- Total entities validated
- Schema violations (count and list)
- 100% validity is required for PASS.

### 3.4 Entity Retention Diff

Aggregate entity counts (§3.1) can **mask deletion bugs**: a variant that drops 5 distinct entities while adding 5 new ones shows a net delta of 0, hiding the loss. This is exactly how PR #393 (#394, 27% loss) and the stale-sweep over-removal (#441) went undetected. A per-entity retention diff compares entities between variant A and variant B — matching by ID and, when IDs differ across branches, by name/alias — so genuine removals are surfaced explicitly and ID-scheme renames are not mistaken for churn.

Run `tools/entity_retention_diff.py`, comparing one representative variant-A run against one representative variant-B run (use the same run number for both, e.g. `run1`):

```bash
python tools/entity_retention_diff.py \
    --variant-a framework-ab-a-run1 \
    --variant-b framework-ab-b-run1
```

The tool accepts either a framework directory (containing a `catalogs/` subdir) or a `catalogs/` directory directly. For each entity type (characters, locations, items, factions, events) it reports:

- **retained** — entities matched between A and B with the *same* ID
- **renamed** — entities matched by name/alias but with a *different* ID (an ID-scheme rename, e.g. `char-elder` → `char-elder-001`)
- **removed** — entities in A with no ID *or* name/alias match in B (TRUE removal)
- **added** — entities in B with no ID *or* name/alias match in A (TRUE addition)

By default (`--match-by auto`) the tool first pairs entities by exact ID, then falls back to a normalized name + alias match (within the same catalog type) for any leftovers. This prevents **phantom churn** when two branches use different ID schemes for the same entity (e.g. main's bare slug `char-elder` vs the compression branch's `char-elder-001`): the entity is reported as a **rename**, not as one removal plus one addition. Use `--match-by id` to restore the legacy exact-ID-only behavior, or `--match-by name` to pair purely on name/alias.

It emits a Markdown summary table (default) or JSON (`--json`), and **flags** the run when the total number of TRUE removed entities exceeds the configurable `--threshold`. ID renames never count toward the threshold. Because the backend is non-deterministic (§1.2), the threshold is **no longer fixed at 0** — it is set to the noise floor measured in §1.4. For each B-vs-A run pair, set `--threshold` to `NF_total` so that churn within the backend noise floor does not flag:

```bash
# Compare each B run against the same-numbered A run; --threshold = measured NF_total
python tools/entity_retention_diff.py -a framework-ab-a-run1 -b framework-ab-b-run1 --threshold <NF_total>
python tools/entity_retention_diff.py -a framework-ab-a-run2 -b framework-ab-b-run2 --threshold <NF_total>
python tools/entity_retention_diff.py -a framework-ab-a-run3 -b framework-ab-b-run3 --threshold <NF_total>
```

For each entity type, take the **maximum** B-vs-A removed count across the run pairs as the observed signal `R_type` (and `R_total` for the aggregate). The gate compares `R_type` against `NF_type` per the tolerance band in §3.5.

**This diff is a required output** — include the Markdown table in the PR comment (see §5.1) and list any removed entity IDs. Removed IDs are not automatically a BLOCK (B may legitimately consolidate duplicates, and removals within the noise floor are backend noise), but each removed ID that **exceeds** the noise floor MUST be explained in the PR comment.

### 3.5 Quantitative Regression Thresholds

| Metric | PASS | WARN | BLOCK |
|---|---|---|---|
| Entity count **loss** | Δ ≤ 5% loss | 5–15% loss | > 15% loss |
| Entity count **gain** | Δ ≤ 10% gain | 10–20% gain | > 20% gain (hallucination signal) |
| Single type count **loss** | Δ ≤ 10% loss | 10–20% loss | > 20% loss |
| Single type count **gain** | Δ ≤ 15% gain | 15–25% gain | > 25% gain (hallucination signal) |
| Relationship count **loss** | Δ ≤ 10% loss | 10–20% loss | > 20% loss |
| Relationship count **gain** | Δ ≤ 15% gain | 15–25% gain | > 25% gain (hallucination signal) |
| Entity **retention** (per type and total) | `R ≤ NF` (within noise floor) | `NF < R ≤ 2·NF + 1` (above noise floor; explain each removed ID as dedup/consolidation) | `R > 2·NF + 1` (exceeds noise band) **or** any unexplained removed ID in the WARN band |
| Schema validity | 100% | — | < 100% |
| Performance regression | Δ ≤ +10% time | +10–20% time | > +20% time |
| Performance improvement | Always PASS | — | — |

A single BLOCK on any metric blocks the PR. WARN requires documented justification.

> **Retention tolerance band.** `R` is the observed B-vs-A removed count (max across run pairs, §3.4) and `NF` is the A-vs-A noise floor (§1.4), evaluated **per entity type** and for the **total**. A type BLOCKs if either its per-type band or the total band is exceeded.
>
> - **PASS:** `R ≤ NF` — the removal is indistinguishable from backend run-to-run noise.
> - **WARN:** `NF < R ≤ 2·NF + 1` — above noise; acceptable only if every removed entity ID in this band is explained as intentional dedup/consolidation.
> - **BLOCK:** `R > 2·NF + 1` — the signal exceeds twice the noise floor and cannot be attributed to backend variance.
>
> **Renames never count** toward `R` (ID-scheme churn; use `--match-by auto`). The `2·` multiplier requires a real regression to exceed twice the measured noise (a conservative 2:1 SNR detection boundary); the `+1` absorbs single-entity flapping at small counts, since entities are discrete and a one-entity difference near `NF=0` is within quantization noise. Both constants are **chosen, not inherited** (Rule 10) and must be revisited if the model or backend changes (re-measure `NF` per §1.4 on every run set — `NF` is never carried over from a prior PR).

---

## 4. Quality Metrics — Semantic

### 4.1 Ground Truth Validation

Two ground truth fixtures exist:

- **`extraction-ground-truth-full-session.json`** — uses the tool-compatible schema (`expected_independent_characters`, `expected_pc_aliases`, `must_not_merge`, `coreference_groups`, etc.). This is the fixture `validate_extraction.py` expects.
- **`extraction-ground-truth-turns-1-30.json`** — uses entity-level keys (`expected_characters`, `expected_locations`, etc.) for manual semantic review. This fixture is **NOT compatible** with `validate_extraction.py`.

Choose the validation approach based on the turn range used:

- **Turns 1–30 runs** (`--max-turns 30`): Validate with `extraction-ground-truth-turns-1-30.json` for entity-level manual review. Note: this fixture is **NOT compatible** with `validate_extraction.py` (different schema) — use it for manual spot-checks only.
- **Full-session runs** (all turns): Validate with `extraction-ground-truth-full-session.json` using `validate_extraction.py`:

```bash
python tools/validate_extraction.py \
    --catalog-dir framework-ab-a-run1/catalogs \
    --ground-truth tests/fixtures/extraction-ground-truth-full-session.json
```

> **Note:** `validate_extraction.py` requires the full-session fixture schema (`expected_independent_characters`, `must_not_merge`, etc.). If you ran extraction on turns 1–30 only, use schema validation (`validate.py`) and manual review against the turns-1-30 fixture instead.

The validation tool checks:

| Check | What it validates |
|---|---|
| Independent Characters | All expected characters exist as separate entities |
| PC Aliases | Player character aliases are correctly merged |
| Must-Not-Merge | Distinct characters are not falsely merged |
| Coreference Groups | Shared-identity entities are properly consolidated |
| Staleness | Entities have recent `last_updated_turn` values |
| Dangling Relationships | No relationships point to nonexistent entities |
| Duplicate Relationships | No redundant relationship entries |
| Locations | Expected locations exist with correct types |
| Factions | Expected factions exist with correct types |

**Report format:** Include the full validation scorecard for both A and B:

```
| Check                  | A: PASS/WARN/FAIL | B: PASS/WARN/FAIL |
|------------------------|--------------------|--------------------|
| Independent Characters | PASS (5/5)         | PASS (5/5)         |
| PC Aliases             | PASS               | PASS               |
| Must-Not-Merge         | PASS               | WARN (1 issue)     |
| ...                    | ...                | ...                |
```

### 4.2 Entity Description Accuracy (Turns 1–30)

> ⚠️ **MANUAL — not yet automated**
>
> These checks require human review. They do NOT block merge but SHOULD be performed for major template changes. See §8 for planned automation.

For each expected entity in the ground truth fixture, verify:

1. **Attribute presence:** Check that `expected_attributes` (e.g., `appearance`, `role`) are populated (non-empty) in the extracted entity.
2. **First-seen turn range:** The entity's `first_seen_turn` falls within the ground truth's `first_seen_turn_range`.
3. **Type correctness:** The entity's type matches the expected type (not misclassified as a different entity type).

Score: `attributes_present / attributes_expected` across all ground truth entities.

| Score | Status |
|---|---|
| ≥ 90% | PASS |
| 75–89% | WARN |
| < 75% | NEEDS REVIEW (advisory — requires written justification; does not auto-prevent merge) |

### 4.3 Relationship Correctness (Turns 1–30)

> ⚠️ **MANUAL — not yet automated**
>
> These checks require human review. They do NOT block merge but SHOULD be performed for major template changes. See §8 for planned automation.

Using the ground truth `coreference_checks`:

1. **Faction membership:** Both captors share faction membership (expected: 1 faction for captors).
2. **Identity consolidation:** Authority-leader described differently across turns is ONE character entry.
3. **Type correctness:** Encampment/fire-gathering are typed as `location`, not `character` or `faction`.

All coreference checks must PASS. Any FAIL warrants written justification before merge (advisory — does not auto-block, but SHOULD be resolved or documented).

### 4.4 Hallucination Detection

> ⚠️ **MANUAL — not yet automated**
>
> These checks require human review. They do NOT block merge but SHOULD be performed for major template changes. See §8 for planned automation.

For each extraction output, identify entities that cannot be traced to the transcript:

1. List all entity IDs in the extraction output.
2. For entities with `first_seen_turn`, verify the entity name or a recognizable alias appears in that turn's transcript text.
3. Entities failing this check are **potential hallucinations**.

| Hallucination rate | Status |
|---|---|
| 0% | PASS |
| ≤ 5% | WARN |
| > 5% | NEEDS REVIEW (advisory — requires written justification; does not auto-prevent merge) |

### 4.5 Dedup Quality

> ⚠️ **MANUAL — not yet automated**
>
> These checks require human review. They do NOT block merge but SHOULD be performed for major template changes. See §8 for planned automation.

Check for phantom/duplicate entities:

1. **Name overlap:** No two entities of the same type should share >50% of name tokens.
2. **ID stem overlap:** No two entity IDs should share the same stem after removing turn suffixes (e.g., `char-shaman-turn-082` and `char-shaman`).
3. Run `python tools/dedup_audit.py --catalog-dir <framework-dir>/catalogs` to detect duplicates automatically.

Report count of suspected duplicates per variant.

### 4.6 Heuristic Checks (Beyond Ground Truth)

> ⚠️ **MANUAL — not yet automated**
>
> These checks require human review. They do NOT block merge but SHOULD be performed for major template changes. See §8 for planned automation.

For turns beyond the ground truth range (31–345), apply these manual heuristics:

1. **Monotonic growth:** Entity count should not decrease between extraction checkpoints.
2. **No empty identity:** Every entity should have a non-empty `identity` field (the `identity` top-level string holds the stable description).
3. **Turn coverage:** Every DM turn in the range should produce at least one discovery or update (check extraction log).
4. **Relationship reciprocity:** If A→B relationship exists, `relationship-index.json` should contain an inferred reverse edge from B→A (note: this checks the index's inferred edges, not necessarily a mirrored relationship entry in B's catalog file).

---

## 5. Reporting Format

### 5.1 PR Comment Template

Every template-change PR MUST include this section as a PR comment:

````markdown
## A/B Test Results

### Configuration
- Model: [model name and quantization]
- Hardware: [GPU(s) used]
- Turns: [range]
- Runs per variant: [N] (minimum 3 at any temperature — backend is non-deterministic, see §1.2)
- Temperature: [value]
- Config: `config/llm.json` (unchanged between A/B)

> `mean ± σ` is reported for all metrics at every temperature (≥3 runs always). The backend is non-deterministic even at temperature 0 (determinism baseline `eval-main-determinism-temp0`), so the entity-retention gate is evaluated against the measured A-vs-A noise floor below, not a zero-removal expectation.

### Performance

| Metric | A (main) mean ± σ | B (branch) mean ± σ | Δ% | Status |
|---|---|---|---|---|
| Wall-clock time (total) | | | | |
| Time per turn | | | | |
| LLM calls per turn | | | | |

### Entity Counts

| Type | A mean ± σ | B mean ± σ | Δ% | Status |
|---|---|---|---|---|
| Characters | | | | |
| Locations | | | | |
| Items | | | | |
| Factions | | | | |
| Events | | | | |
| **Total** | | | | |

### Noise Floor (A-vs-A)

Pairwise self-comparison of the variant-A runs (§1.4), `--match-by auto` (renames excluded). This is the tolerance band for the retention gate.

| Type | A-vs-A removed (max) | A-side count | % noise floor | NF_type |
|---|---|---|---|---|
| characters | | | | |
| locations | | | | |
| items | | | | |
| factions | | | | |
| events | | | | |
| **Total** | | | | |

Pairwise comparisons used: run1↔run2, run1↔run3, run2↔run3.

### Entity Retention Diff

(Output of `tools/entity_retention_diff.py` aggregated across the B-vs-A run pairs; `Removed (R)` is the per-type maximum removed across those pairs.)

| Type | A | B | Retained | Removed (R) | Added | NF_type | Band (PASS/WARN/BLOCK) |
|---|---|---|---|---|---|---|---|
| characters | | | | | | | |
| locations | | | | | | | |
| items | | | | | | | |
| factions | | | | | | | |
| events | | | | | | | |
| **Total** | | | | | | | |

`R` = max removed across B-vs-A run pairs. Band per §3.5: PASS `R ≤ NF`, WARN `NF < R ≤ 2·NF+1` (explain each removed ID), BLOCK `R > 2·NF+1`.

Removed entity IDs above the noise floor (explain each): [list, or "none"]

### Relationships

| Metric | A mean ± σ | B mean ± σ | Δ% | Status |
|---|---|---|---|---|
| Total relationships | | | | |

### Ground Truth Validation (full-session runs only)

> **Note:** This section applies only to full-session runs using `validate_extraction.py` with `extraction-ground-truth-full-session.json`. For turns 1–30 runs, use schema validation (`validate.py --framework <dir>`) and manual review against `extraction-ground-truth-turns-1-30.json`.

| Check | A | B |
|---|---|---|
| Independent Characters | | |
| PC Aliases | | |
| Must-Not-Merge | | |
| Coreference Groups | | |
| Staleness | | |
| Dangling Relationships | | |
| Duplicate Relationships | | |
| Locations (late-game) | | |
| Factions (late-game) | | |

### Semantic Quality

| Metric | A | B | Status |
|---|---|---|---|
| Attribute completeness | | | |
| First-seen accuracy | | | |
| Hallucination rate | | | |
| Schema validity | | | |
| Suspected duplicates | | | |

### Verdict

- [ ] No BLOCK on any metric
- [ ] All WARN items have documented justification
- [ ] A-vs-A noise floor measured and reported (§1.4)
- [ ] Every removed entity ID **above** the noise floor (WARN band) is explained (dedup/consolidation); any removal in the BLOCK band blocks the PR
- [ ] Ground truth validation passes for B (full-session runs only; skip for `--max-turns 30`)
````

### 5.2 Pass/Fail Summary

The PR is **mergeable** when:

1. Zero BLOCK statuses across all metrics.
2. All WARN statuses have written justification explaining why the regression is acceptable.
3. Ground truth validation (`validate_extraction.py`) exits 0 for variant B (full-session runs only).
4. Schema validation (`validate.py --framework <dir>`) reports 0 violations for variant B.
5. Every removed entity ID **exceeding the A-vs-A noise floor** (§1.4) is explained as intentional dedup/consolidation; removals in the BLOCK band (`R > 2·NF+1`) are a BLOCK regardless of explanation.

---

## 6. Execution Instructions

### 6.1 Environment Setup

Ensure `config/llm.json` is configured and both LLM servers are running:

```bash
# Verify servers are reachable (default ports — adjust to match your config/llm.json)
curl -s http://localhost:8080/v1/models | python -m json.tool
curl -s http://localhost:8081/v1/models | python -m json.tool
```

### 6.2 Run Variant A (Baseline)

> **Note:** The multi-run (run1/run2/run3) commands below are required at **every** temperature — a minimum of 3 runs per variant applies because the backend is non-deterministic even at temperature 0 (see §1.2). The variant-A runs also feed the A-vs-A noise-floor baseline (§1.4).

```bash
# From the repo root, on main branch
git stash  # if needed
git checkout main
git pull

# Run extraction — turns 1-30, separate --framework per run
# Run 1:
python tools/bootstrap_session.py \
    --session sessions/session-import \
    --file sessions/session-import/raw/full-transcript.md \
    --framework framework-ab-a-run1 \
    --max-turns 30 \
    --overwrite \
    --no-resume \
    --base-url http://localhost:8080/v1

# Run 2:
python tools/bootstrap_session.py \
    --session sessions/session-import \
    --file sessions/session-import/raw/full-transcript.md \
    --framework framework-ab-a-run2 \
    --max-turns 30 \
    --overwrite \
    --no-resume \
    --base-url http://localhost:8080/v1

# Run 3:
python tools/bootstrap_session.py \
    --session sessions/session-import \
    --file sessions/session-import/raw/full-transcript.md \
    --framework framework-ab-a-run3 \
    --max-turns 30 \
    --overwrite \
    --no-resume \
    --base-url http://localhost:8080/v1
```

> **Note:** Always specify `--base-url` explicitly to prevent round-robin mixing between A and B variants. Substitute `localhost:8080` / `localhost:8081` with your actual server endpoints from `config/llm.json` `base_urls`.
>
> **Note:** The `--session` and `--file` paths refer to a locally prepared import session. Place your transcript at `sessions/session-import/raw/full-transcript.md` and create the session directory together with its `raw/` subdirectory (`mkdir -p sessions/session-import/raw`). These paths are not committed to the repository; see `docs/usage.md` for instructions on setting up a session before running A/B tests.

### 6.3 Run Variant B (Candidate)

> **Note:** As in §6.2, the multi-run (run1/run2/run3) commands below are required at **every** temperature — a minimum of 3 runs per variant applies because the backend is non-deterministic even at temperature 0 (see §1.2).

```bash
git checkout <pr-branch>

# Run extraction — same parameters, different output dir, separate --framework per run
# Run 1:
python tools/bootstrap_session.py \
    --session sessions/session-import \
    --file sessions/session-import/raw/full-transcript.md \
    --framework framework-ab-b-run1 \
    --max-turns 30 \
    --overwrite \
    --no-resume \
    --base-url http://localhost:8081/v1

# Run 2:
python tools/bootstrap_session.py \
    --session sessions/session-import \
    --file sessions/session-import/raw/full-transcript.md \
    --framework framework-ab-b-run2 \
    --max-turns 30 \
    --overwrite \
    --no-resume \
    --base-url http://localhost:8081/v1

# Run 3:
python tools/bootstrap_session.py \
    --session sessions/session-import \
    --file sessions/session-import/raw/full-transcript.md \
    --framework framework-ab-b-run3 \
    --max-turns 30 \
    --overwrite \
    --no-resume \
    --base-url http://localhost:8081/v1
```

> **Note:** Always specify `--base-url` explicitly to prevent round-robin mixing between A and B variants.

### 6.4 Parallel A/B Using Two GPUs

The two B70 GPU servers (ports 8080 and 8081 by default — see `tools/submit_ab_test.py`) can run A and B simultaneously:

```powershell
# Terminal 1 — Variant A on GPU 0 (port 8080)
python tools/bootstrap_session.py `
    --session sessions/session-import `
    --file sessions/session-import/raw/full-transcript.md `
    --framework framework-ab-a-run1 `
    --max-turns 30 `
    --base-url http://localhost:8080/v1 `
    --overwrite `
    --no-resume

# Terminal 2 — Variant B on GPU 1 (port 8081)
python tools/bootstrap_session.py `
    --session sessions/session-import `
    --file sessions/session-import/raw/full-transcript.md `
    --framework framework-ab-b-run1 `
    --max-turns 30 `
    --base-url http://localhost:8081/v1 `
    --overwrite `
    --no-resume
```

> **Note:** When running parallel A/B, each variant must use a separate `--base-url` to avoid round-robin mixing. Do NOT rely on the default round-robin in `config/llm.json` — it would mix responses across A/B runs.

### 6.5 Validation

#### Turns 1–30 runs (default)

Schema validation is always required. After all runs complete:

```bash
for run in framework-ab-a-run1 framework-ab-a-run2 framework-ab-a-run3 \
           framework-ab-b-run1 framework-ab-b-run2 framework-ab-b-run3; do
    python tools/validate.py --framework "$run"
done
```

For semantic review, compare extraction output against `tests/fixtures/extraction-ground-truth-turns-1-30.json` manually. The turns-1-30 fixture uses entity-level keys (`expected_characters`, `expected_locations`, etc.) and is **not compatible** with `validate_extraction.py`.

#### Full-session runs only

In addition to schema validation above, run ground truth validation using `validate_extraction.py` with the full-session fixture:

```bash
python tools/validate_extraction.py \
    --catalog-dir framework-ab-a-run1/catalogs \
    --ground-truth tests/fixtures/extraction-ground-truth-full-session.json

python tools/validate_extraction.py \
    --catalog-dir framework-ab-b-run1/catalogs \
    --ground-truth tests/fixtures/extraction-ground-truth-full-session.json
```

### 6.6 Collecting Metrics

#### Entity Counts

```bash
# Count entities per type in a catalog directory (per-run, both variants)
for run in framework-ab-a-run1 framework-ab-a-run2 framework-ab-a-run3 \
           framework-ab-b-run1 framework-ab-b-run2 framework-ab-b-run3; do
  echo "=== $run ==="
  # Characters: exclude char-player.json, index.json, and sidecar files
  echo "characters: $(ls $run/catalogs/characters/*.json 2>/dev/null | grep -v 'char-player\.json' | grep -v 'index\.json' | grep -v '\.arcs\.json' | grep -v '\.synthesis\.json' | wc -l)"
  for type in locations items factions; do
    echo "$type: $(ls $run/catalogs/$type/*.json 2>/dev/null | grep -v 'index\.json' | grep -v '\.arcs\.json' | grep -v '\.synthesis\.json' | wc -l)"
  done
  _evcount=$(python -c 'import json,sys; print(len(json.load(open(sys.argv[1], encoding="utf-8-sig"))))' \
    "$run/catalogs/events.json" 2>/dev/null || echo 0)
  echo "events: $_evcount"
done
```

PowerShell equivalent:

```powershell
foreach ($run in @(
    "framework-ab-a-run1", "framework-ab-a-run2", "framework-ab-a-run3",
    "framework-ab-b-run1", "framework-ab-b-run2", "framework-ab-b-run3")) {
    Write-Output "=== $run ==="
    # Characters: exclude char-player.json, index.json, and sidecar files
    $charCount = (Get-ChildItem "$run/catalogs/characters/*.json" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne "char-player.json" -and $_.Name -ne "index.json" -and $_.Name -notlike "*.arcs.json" -and $_.Name -notlike "*.synthesis.json" }).Count
    Write-Output "characters: $charCount"
    foreach ($type in @("locations", "items", "factions")) {
        $count = (Get-ChildItem "$run/catalogs/$type/*.json" -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -ne "index.json" -and $_.Name -notlike "*.arcs.json" -and $_.Name -notlike "*.synthesis.json" }).Count
        Write-Output "${type}: $count"
    }
    $eventsFile = "$run/catalogs/events.json"
    if (Test-Path $eventsFile) {
        $events = (Get-Content $eventsFile -Raw | ConvertFrom-Json)
        Write-Output "events: $($events.Count)"
    } else { Write-Output "events: 0" }
}
```

#### Relationship Counts

```bash
# Count total relationships across all entity files (per-run, both variants)
for run in framework-ab-a-run1 framework-ab-a-run2 framework-ab-a-run3 \
           framework-ab-b-run1 framework-ab-b-run2 framework-ab-b-run3; do
  echo "=== $run ==="
  python -c "
import json, glob
total = 0
for f in glob.glob('$run/catalogs/**/*.json', recursive=True):
    d = json.load(open(f, encoding="utf-8-sig"))
    if isinstance(d, dict):
        total += len(d.get('relationships', []))
print(f'relationships: {total}')
"
done
```

PowerShell equivalent:

```powershell
# Count total relationships across all entity files (per-run, both variants)
foreach ($run in @(
    "framework-ab-a-run1", "framework-ab-a-run2", "framework-ab-a-run3",
    "framework-ab-b-run1", "framework-ab-b-run2", "framework-ab-b-run3")) {
    $total = 0
    Get-ChildItem "$run/catalogs/" -Filter *.json -Recurse |
        Where-Object { $_.Name -ne "index.json" -and $_.Name -ne "events.json" } |
        ForEach-Object {
            $json = Get-Content $_ -Raw | ConvertFrom-Json
            if ($json -is [PSCustomObject] -and $json.relationships) {
                $total += $json.relationships.Count
            }
        }
    Write-Output "${run} relationships: $total"
}
```

#### Wall-Clock Time

Record timestamps before and after each extraction run. The extraction pipeline logs per-turn timing to stdout — capture it:

```powershell
$start = Get-Date
python tools/bootstrap_session.py ... 2>&1 | Tee-Object -FilePath "ab-a-run1.log"
$elapsed = (Get-Date) - $start
Write-Output "Total time: $($elapsed.TotalMinutes) minutes"
```

### 6.7 Computing Statistics

For N runs of each variant, compute mean and standard deviation:

```python
import statistics
# Example: entity counts from 3 runs
a_counts = [45, 47, 46]
b_counts = [42, 43, 41]
print(f"A: {statistics.mean(a_counts):.1f} ± {statistics.stdev(a_counts):.1f}")
print(f"B: {statistics.mean(b_counts):.1f} ± {statistics.stdev(b_counts):.1f}")
delta_pct = (statistics.mean(b_counts) - statistics.mean(a_counts)) / statistics.mean(a_counts) * 100
print(f"Δ: {delta_pct:+.1f}%")
```

---

## 7. Exemptions

The following changes do NOT require A/B testing:

- Changes to templates **outside** `templates/extraction/` (e.g., `dm-profile-analyzer.md`, `dm-*.md`). Note: extraction templates are loaded verbatim by the pipeline — any text change, including formatting or comments, alters the prompt and requires A/B testing.
- New template files not yet wired into the extraction pipeline

To claim an exemption, state the reason in the PR description under an "A/B Test Exemption" heading.

---

## 8. Future Improvements

When tooling matures, the following should be automated:

- [ ] `tools/ab_test.py` — orchestrates multi-run extraction, collects metrics, generates report
- [ ] CI integration — run A/B on every template-change PR via GitHub Actions
- [ ] Statistical significance testing (paired t-test or Wilcoxon) for metric deltas
- [ ] Token-level tracking (input/output tokens per LLM call) once extraction logging supports it
- [ ] Automated hallucination detection against transcript text
