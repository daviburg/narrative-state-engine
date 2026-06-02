# Model Evaluation Standard for Model-vs-Model Comparisons

This document defines the required evaluation gate for any decision to change the LLM model used by the semantic extraction pipeline. It complements `docs/ab-test-standard.md`, which governs template changes — that standard fixes the model and varies the template; this standard fixes the template and varies the model.

**Scope:** Any proposal to change the `model` field (or equivalent) in `config/llm.json` MUST include a model evaluation report before the change is merged or applied to production sessions.

**Background:** This standard was created after a Qwen3-8B vs Qwen3-30B-A3B evaluation (May 2026) revealed that raw entity counts are an unreliable primary metric for model selection. The 8B model produced more entities than the 30B model, yet LLM-as-judge semantic scoring showed the opposite quality ordering (8B: 5.9/10 vs 30B: 7.4/10). The discrepancy was caused by dedup failures in the 8B model producing phantom duplicate entities that inflated the count. Conclusions flipped depending on which partial metric was examined first. This standard prevents partial-metric conclusions by requiring a composite score.

**Part of:** Epic #97 (Quality Evaluation Pipeline).

---

## 1. Evaluation Scope

### 1.1 Turn Selection

| Parameter | Minimum | Recommended | Major decisions |
|---|---|---|---|
| Ground truth range | Turns 1–30 | Turns 1–50 | Full session (all turns) |

- **Turns 1–30** are always required. Ground truth fixtures at `tests/fixtures/extraction-ground-truth-turns-1-30.json` provide entity-level validation.
- **Turns 1–50** are recommended for evaluations that will affect any future session.
- **Full session** should be used when switching the primary production model, downgrading from a larger to smaller model, or when turns 1–50 results are ambiguous.

### 1.2 Runs Per Model

Non-determinism at temperature > 0 requires multiple runs for statistical validity.

| Temperature | Minimum runs | Recommended runs |
|---|---|---|
| temperature > 0 | 3 | 5 |
| temperature = 0 | 1 | 1 |

Report **mean ± standard deviation** for all metrics when running multiple runs. If any metric's standard deviation exceeds 15% of its mean, increase to 5 runs before drawing conclusions.

> **Rationale:** temperature = 0 is deterministic — a single run is sufficient. At temperature > 0, the minimum of 3 runs provides enough variance information to detect instability without requiring overnight compute budgets.

### 1.3 Variant Definitions

- **Model A (baseline):** The model currently in production `config/llm.json`. Use `--framework framework-eval-a-runN` output directories.
- **Model B (candidate):** The new model under evaluation. Use `--framework framework-eval-b-runN` output directories.
- Both models MUST use identical templates from `main` HEAD, an identical `config/llm.json` (with per-run model and endpoint differences supplied via `--model` and `--base-url` CLI overrides rather than by editing the config — this ensures `base_url`, `base_urls`, and all other settings are held constant between runs), and identical hardware where possible.

---

## 2. Semantic Quality Scoring (LLM-as-Judge)

Semantic scoring is the **primary quality signal** for model evaluation. Entity counts alone are insufficient (see Background above).

### 2.1 Scoring Rubric

An LLM judge evaluates each extraction output against the source transcript on a **1–10 integer scale** across five dimensions:

| Dimension | Weight | What it measures |
|---|---|---|
| **Accuracy** | 25% | Are extracted attributes correct? Does entity identity match the transcript? Are relationships factually grounded? |
| **Completeness** | 25% | Are all narratively significant entities, relationships, and events captured? Does coverage scale appropriately with turn range? |
| **Hallucination absence** | 20% | Are there entities, attributes, or relationships that cannot be traced to the transcript? (Lower hallucination = higher score) |
| **Dedup quality** | 20% | Are distinct entities kept distinct? Are the same entity's mentions properly merged? Are there phantom duplicates? |
| **Attribute richness** | 10% | Are entity records populated with meaningful attributes beyond minimal stubs? Do volatile state fields update as the transcript progresses? |

**Scoring scale:**

| Score | Meaning |
|---|---|
| 9–10 | Excellent — extraction matches or exceeds human-level quality |
| 7–8 | Good — minor gaps or minor issues; usable without manual correction |
| 5–6 | Adequate — noticeable gaps or issues; requires spot-checking |
| 3–4 | Poor — significant gaps or errors; requires manual correction |
| 1–2 | Failing — extraction is unreliable; cannot be used directly |

### 2.2 Judge Prompt

Automated LLM-as-judge evaluation is a planned feature (tracked under §11 / epic #97). Until it is implemented, perform the evaluation manually using the rubric in §2.1. Submit the extraction output and source transcript to the judge model and record per-dimension scores. Document the judge model and version used.

### 2.3 Semantic Score Aggregation

Compute the **weighted semantic score** for each run:

```
semantic_score = (accuracy × 0.25) + (completeness × 0.25) + (hallucination_absence × 0.20) + (dedup_quality × 0.20) + (attribute_richness × 0.10)
```

Report the mean ± σ across runs.

---

## 3. Composite Quality Metric

The composite score combines quantitative and semantic signals into a single comparable number. It prevents single-metric conclusions by requiring all dimensions to be represented.

### 3.1 Formula

```
composite = (entity_count_score × 0.20) + (dedup_score × 0.25) + (attribute_completeness × 0.20) + (semantic_score × 0.35)
```

All input values are normalized to a 0–10 scale before applying weights.

### 3.2 Input Normalization

| Input | Normalization |
|---|---|
| `entity_count_score` | `min(10, (entity_count / expected_entity_count) × 10)` — capped at 10 to prevent inflated counts from dominating |
| `dedup_score` | `max(0, 10 - (suspected_duplicate_rate × 200))` — `suspected_duplicate_rate` is a 0.0–1.0 fraction (e.g. 0.00 = 0% duplicates → score 10; 0.05 = 5% duplicates → score 0) |
| `attribute_completeness` | `(attributes_present / attributes_expected) × 10` — sum `expected_attributes` list lengths across all entries in all entity categories of the ground truth fixture to get `attributes_expected`; count how many of those attributes are non-empty in the extracted entities to get `attributes_present` |
| `semantic_score` | Raw LLM-as-judge weighted score (already on 0–10 scale) |

### 3.3 Weight Justification

| Weight | Dimension | Justification |
|---|---|---|
| 35% | Semantic score | Human-interpretable quality judgment; most comprehensive signal; captures what counts are blind to |
| 25% | Dedup score | Dedup failures directly inflate counts and corrupt downstream analysis; high weight prevents masking |
| 20% | Attribute completeness | Stub-only entities have limited utility for planning and strategy agents |
| 20% | Entity count score | Coverage matters, but capped to prevent phantom-count inflation |

> **Note:** The 35% semantic weight reflects the lesson from the 8B vs 30B comparison: dedup failures can make a lower-quality model appear better on raw counts. Semantic scoring is the corrective signal.

### 3.4 Expected Entity Count

Use the ground truth fixture's entity count as `expected_entity_count`, or derive it from the baseline (Model A mean) if no fixture is available. Document which source was used.

---

## 4. Dedup Quality as a Blocking Gate

Dedup quality is both a component of the composite score (§3) **and** an independent blocking gate. A model that produces significant phantom duplicates is structurally unreliable regardless of its other scores.

### 4.1 Dedup Audit Procedure

Run `tools/dedup_audit.py` on each extraction output:

```bash
python tools/dedup_audit.py --catalog-dir framework-eval-b-run1/catalogs
```

The tool reports:
- Number of heuristic candidate pairs found (edit distance ≤ 3, substring match, or same-turn + similar name)
- Auto-merged count (LLM confidence ≥ 0.9)
- Flagged for review count (LLM confidence 0.6–0.9)
- Discarded count (LLM scored as distinct, or confidence < 0.6)

> **Note:** ID-stem duplicate detection (same entity stem, different turn suffix) is a planned feature, not yet implemented. Check for such duplicates manually when reviewing the flagged pairs.

### 4.2 Dedup Blocking Thresholds

| Suspected duplicate rate | Action |
|---|---|
| 0–2% | PASS — acceptable dedup quality |
| 2–5% | WARN — document pairs; verify they are genuinely distinct; acceptable if justified |
| > 5% | **BLOCK** — model cannot be adopted; dedup failures will corrupt entity counts and composite scores |

`suspected_duplicate_rate = suspected_duplicate_pairs / total_entities`

`suspected_duplicate_rate` is a 0.0–1.0 fraction (not a 0–100 percentage). For example, 3 suspected pairs out of 100 entities → `suspected_duplicate_rate = 0.03`.

where `suspected_duplicate_pairs = auto_merged + flagged_for_review` from the `dedup_audit.py` output (auto-merged pairs are confirmed duplicates; flagged-for-review pairs are suspected duplicates pending manual confirmation; discarded pairs are not counted).

A **BLOCK on dedup** prevents adoption regardless of composite score. This is the lesson from the 8B evaluation: a model that cannot reliably deduplicate produces misleading metrics across all downstream analysis.

### 4.3 Per-Run Dedup Reporting

For temperature > 0 evaluations, report dedup results for every run (not just the mean), because dedup instability across runs is itself a signal of poor model reliability.

---

## 5. Quantitative Metrics

Quantitative metrics are secondary to the composite score but are required for the report.

### 5.1 Entity Counts

Count entities by type using the same method as `docs/ab-test-standard.md` §3.1.

### 5.2 Relationship Counts

Count total relationships across all entity files using the same method as `docs/ab-test-standard.md` §3.2.

### 5.3 Schema Validity

Run `python tools/validate.py --framework <dir>` on each output. 100% schema validity is required.

### 5.4 Performance Metrics

| Metric | Source |
|---|---|
| Wall-clock time per turn | `elapsed_ms / 1000` from extraction log |
| Total extraction time | Start-to-finish wall clock |
| LLM calls per turn | Sum of `prompt_metrics.<phase>.calls` across phases |
| Throughput | turns / minute |

Performance is a secondary consideration — it does not override quality outcomes — but must be reported for cost and feasibility analysis.

### 5.5 Quantitative Thresholds

These thresholds apply to the candidate model (B) vs baseline (A):

| Metric | PASS | WARN | BLOCK |
|---|---|---|---|
| Entity count loss | Δ ≤ 5% loss | 5–15% loss | > 15% loss |
| Entity count gain | Δ ≤ 10% gain | 10–20% gain | > 20% gain (hallucination signal) |
| Schema validity | 100% | — | < 100% |
| Suspected duplicates | ≤ 2% | 2–5% | > 5% |
| Performance regression | Δ ≤ +20% time | +20–40% time | > +40% time |
| Performance improvement | Always PASS | — | — |

> **Note:** Performance regression threshold is more permissive than the template A/B standard (+40% vs +20%) because a better-quality model may be worth the latency cost. Document any regression above +20% with a cost/quality justification.

---

## 6. Decision Criteria

A model switch is approved when **all** of the following conditions are met:

### 6.1 Required Conditions (All Must Pass)

1. **Zero BLOCK statuses** on any metric (dedup, schema, entity count, performance).
2. **Composite score improvement ≥ 10%** over baseline:
   ```
   (composite_B - composite_A) / composite_A ≥ 0.10
   ```
3. **No semantic dimension regression**: No individual LLM-as-judge dimension score for B is more than 1.0 point lower than A on the 1–10 scale.
4. **Dedup gate passes** (≤ 5% suspected duplicates across all runs).
5. **Schema validation passes** (100% validity for B).

### 6.2 Advisory Conditions (Must Be Documented If Not Met)

6. **No entity count loss** > 5% vs baseline.
7. **Performance regression justified**: Any wall-clock regression > +20% must have a documented quality justification (e.g., "30B model is 35% slower but composite score is 23% higher, which is the primary quality signal for production sessions").

### 6.3 When a Switch Is NOT Warranted

Do not switch models if:
- Composite improvement is < 10% (noise-level difference; not worth migration cost).
- Any blocking metric fails, even if composite score looks favorable.
- Results are based on fewer than the minimum required runs (§1.2).
- Evaluation was performed on < turns 1–30 (§1.1).

### 6.4 Documenting the Decision

Every model switch decision — approved or rejected — must be documented in the PR or issue comment with:
- The full evaluation report (§7 template)
- The composite scores for A and B
- The explicit pass/fail on each required condition
- For rejections: the specific failing condition

---

## 7. Reporting Template

Every model evaluation PR or issue comment MUST include this report section:

````markdown
## Model Evaluation Report

### Configuration

- Model A (baseline): [model name, quantization, parameter count]
- Model B (candidate): [model name, quantization, parameter count]
- Hardware: [GPU(s) used]
- Turns: [range, e.g. 1–30]
- Runs per model: [N] (temperature: [value])
- Templates: `main` HEAD (SHA: [short SHA])
- Evaluation date: [YYYY-MM-DD]

### Dedup Quality (Blocking Gate)

| Model | Suspected duplicate pairs | Total entities | Duplicate rate | Status |
|---|---|---|---|---|
| A (baseline) | | | | |
| B (candidate) | | | | |

> ⚠️ If B duplicate rate > 5%, evaluation stops here. BLOCK.

### Semantic Quality (LLM-as-Judge)

Judge model: [model used for evaluation]

| Dimension | Weight | A mean ± σ | B mean ± σ | Δ |
|---|---|---|---|---|
| Accuracy | 25% | | | |
| Completeness | 25% | | | |
| Hallucination absence | 20% | | | |
| Dedup quality | 20% | | | |
| Attribute richness | 10% | | | |
| **Weighted semantic score** | | | | |

### Entity Counts

| Type | A mean ± σ | B mean ± σ | Δ% | Status |
|---|---|---|---|---|
| Characters | | | | |
| Locations | | | | |
| Items | | | | |
| Factions | | | | |
| Events | | | | |
| **Total** | | | | |

### Relationships

| Metric | A mean ± σ | B mean ± σ | Δ% | Status |
|---|---|---|---|---|
| Total relationships | | | | |

### Schema Validity

| Model | Total entities | Schema violations | Status |
|---|---|---|---|
| A (baseline) | | | |
| B (candidate) | | | |

### Performance

| Metric | A mean ± σ | B mean ± σ | Δ% | Status |
|---|---|---|---|---|
| Wall-clock time (total) | | | | |
| Time per turn | | | | |
| LLM calls per turn | | | | |

### Composite Score

| Input | A | B | Weight |
|---|---|---|---|
| Entity count score (normalized 0–10) | | | 20% |
| Dedup score (normalized 0–10) | | | 25% |
| Attribute completeness (normalized 0–10) | | | 20% |
| Semantic score (0–10) | | | 35% |
| **Composite** | | | |

Improvement: [(composite_B - composite_A) / composite_A × 100]%

### Decision

| Condition | Result |
|---|---|
| Zero BLOCKs on any metric | PASS / BLOCK |
| Composite improvement ≥ 10% | PASS / FAIL |
| No semantic dimension regression > 1.0 pt | PASS / FAIL |
| Dedup gate ≤ 5% | PASS / BLOCK |
| Schema validity 100% | PASS / BLOCK |

**Verdict:** APPROVED / REJECTED

If REJECTED: [specific failing condition and recommended next steps]
If APPROVED with WARNs: [documented justifications for each WARN]
````

---

## 8. Execution Instructions

### 8.1 Environment Setup

Configure `config/llm.json` so both model servers are reachable. The run commands in §8.2 and §8.3 assume **two OpenAI-compatible servers** listening on `:8080/v1` (Model A) and `:8081/v1` (Model B) — for example, two `llama.cpp --server` or `vllm` instances, or two Ollama instances started with `OLLAMA_HOST=0.0.0.0:8080` / `:8081`. Verify both endpoints before running:

```bash
# Verify Model A server (port 8080)
curl -s http://localhost:8080/v1/models | python -m json.tool

# Verify Model B server (port 8081)
curl -s http://localhost:8081/v1/models | python -m json.tool
```

> **Alternative — single Ollama instance:** If both models are served by one Ollama process on `:11434`, omit `--base-url` from all commands (or pass `--base-url http://localhost:11434/v1`) and rely solely on `--model` to switch between them. The `--base-url` and `--model` overrides are independent.

### 8.2 Run Model A (Baseline)

> **Note:** The `--session` and `--file` paths refer to a locally prepared import session. Place your transcript at `sessions/session-import/raw/full-transcript.md` and create the session directory together with its `raw/` subdirectory (`mkdir -p sessions/session-import/raw`). The `sessions/session-import` path is listed in `.gitignore` and is not committed to the repository; see `docs/usage.md` for instructions on setting up a local session before running evaluations.

```bash
# Run 1:
python tools/bootstrap_session.py \
    --session sessions/session-import \
    --file sessions/session-import/raw/full-transcript.md \
    --framework framework-eval-a-run1 \
    --max-turns 30 \
    --overwrite \
    --no-resume \
    --model <baseline-model-name> \
    --base-url http://localhost:8080/v1

# Run 2:
python tools/bootstrap_session.py \
    --session sessions/session-import \
    --file sessions/session-import/raw/full-transcript.md \
    --framework framework-eval-a-run2 \
    --max-turns 30 \
    --overwrite \
    --no-resume \
    --model <baseline-model-name> \
    --base-url http://localhost:8080/v1

# Run 3:
python tools/bootstrap_session.py \
    --session sessions/session-import \
    --file sessions/session-import/raw/full-transcript.md \
    --framework framework-eval-a-run3 \
    --max-turns 30 \
    --overwrite \
    --no-resume \
    --model <baseline-model-name> \
    --base-url http://localhost:8080/v1
```

### 8.3 Run Model B (Candidate)

```bash
# Run 1:
python tools/bootstrap_session.py \
    --session sessions/session-import \
    --file sessions/session-import/raw/full-transcript.md \
    --framework framework-eval-b-run1 \
    --max-turns 30 \
    --overwrite \
    --no-resume \
    --model <candidate-model-name> \
    --base-url http://localhost:8081/v1

# Runs 2 and 3: same pattern with framework-eval-b-run2, framework-eval-b-run3
```

### 8.4 Run Dedup Audit

```bash
for run in framework-eval-a-run1 framework-eval-a-run2 framework-eval-a-run3 \
           framework-eval-b-run1 framework-eval-b-run2 framework-eval-b-run3; do
    echo "=== $run ==="
    python tools/dedup_audit.py --catalog-dir "$run/catalogs"
done
```

### 8.5 Schema Validation

```bash
for run in framework-eval-a-run1 framework-eval-a-run2 framework-eval-a-run3 \
           framework-eval-b-run1 framework-eval-b-run2 framework-eval-b-run3; do
    python tools/validate.py --framework "$run"
done
```

### 8.6 Compute Composite Score

After collecting all per-run metrics, compute the composite score (§3.1) for each run, then report the mean across runs.

---

## 9. Example Evaluation Report

This example is based on the May 2026 Qwen3-8B vs Qwen3-30B-A3B evaluation that motivated this standard.

````markdown
## Model Evaluation Report

### Configuration

- Model A (baseline): Qwen3-8B (Q4_K_M, 8B parameters)
- Model B (candidate): Qwen3-30B-A3B (Q4_K_M, 30B MoE, ~3B active)
- Hardware: Intel Arc B70 (dual GPU)
- Turns: 1–30
- Runs per model: 3 (temperature: 0.3)
- Templates: main HEAD (SHA: abc1234)
- Evaluation date: 2026-05-15

### Dedup Quality (Blocking Gate)

| Model | Suspected duplicate pairs | Total entities | Duplicate rate | Status |
|---|---|---|---|---|
| A (Qwen3-8B) | 7 | 52 | 13.5% | **BLOCK** |
| B (Qwen3-30B-A3B) | 1 | 47 | 2.1% | PASS |

> ⚠️ Model A (8B) fails the dedup gate at 13.5% (threshold: 5%). The 8B model's higher raw entity count (52 vs 47) was entirely explained by phantom duplicates.

> **Note:** "Total entities" here counts only the non-event entity types audited by `dedup_audit.py` (characters, locations, items, factions). The Entity Counts table below includes all types including events (events are not subject to the same cross-turn identity-merge dedup). The per-run raw counts feeding this table were 52 (8B) and 47 (30B) for non-event entities.

### Semantic Quality (LLM-as-Judge)

Judge model: gpt-4o

| Dimension | Weight | A (8B) mean ± σ | B (30B) mean ± σ | Δ |
|---|---|---|---|---|
| Accuracy | 25% | 6.3 ± 0.5 | 7.8 ± 0.3 | +1.5 |
| Completeness | 25% | 6.1 ± 0.7 | 7.5 ± 0.4 | +1.4 |
| Hallucination absence | 20% | 5.2 ± 0.8 | 7.6 ± 0.3 | +2.4 |
| Dedup quality | 20% | 4.0 ± 1.1 | 7.8 ± 0.5 | +3.8 |
| Attribute richness | 10% | 6.8 ± 0.4 | 7.2 ± 0.4 | +0.4 |
| **Weighted semantic score** | | **5.9 ± 0.6** | **7.6 ± 0.3** | **+1.7** |

### Entity Counts

| Type | A (8B) mean ± σ | B (30B) mean ± σ | Δ% | Status |
|---|---|---|---|---|
| Characters | 28.3 ± 2.1 | 24.7 ± 0.6 | -12.7% | WARN (phantom reduction) |
| Locations | 11.3 ± 0.6 | 10.7 ± 0.6 | -5.3% | WARN |
| Items | 6.7 ± 0.6 | 6.3 ± 0.6 | -6.0% | WARN |
| Factions | 3.7 ± 0.6 | 3.3 ± 0.6 | -10.8% | WARN |
| Events | 18.0 ± 1.7 | 17.7 ± 0.6 | -1.7% | PASS |
| **Total** | **68.0 ± 3.6** | **62.7 ± 1.5** | **-7.8%** | WARN |

> Note: The 8B model's higher entity counts are not genuine — they reflect phantom duplicates. After dedup, effective unique entities for 8B would be ~52, vs 47 for 30B, a much smaller gap.

### Composite Score

| Input | A (8B) | B (30B) | Weight |
|---|---|---|---|
| Entity count score (normalized) | 10.0 | 9.5 | 20% |
| Dedup score (normalized) | 0.0 (blocked) | 5.8 | 25% |
| Attribute completeness | 6.8 | 7.6 | 20% |
| Semantic score | 5.9 | 7.6 | 35% |
| **Composite** | **5.43** | **7.53** | |

Improvement: +38.7% composite score for 30B model.

### Decision

| Condition | Result |
|---|---|
| Zero BLOCKs on any metric | A: BLOCK (dedup 13.5%), B: PASS |
| Composite improvement ≥ 10% | PASS (+38.7%) |
| No semantic dimension regression > 1.0 pt | PASS (all B dimensions ≥ A) |
| Dedup gate ≤ 5% | B: PASS (2.1%), A: BLOCK (13.5%) |
| Schema validity 100% | PASS (both models) |

**Verdict:** APPROVED — switch from Qwen3-8B to Qwen3-30B-A3B.

The 8B model's apparent entity count advantage is a measurement artifact. The 30B model
is the correct production model at this capability level.
````

---

## 10. Relationship to Other Standards

| Standard | Scope | Fixed | Varied |
|---|---|---|---|
| `docs/ab-test-standard.md` | Template changes | Model, hardware | Prompt templates |
| `docs/model-eval-standard.md` (this document) | Model selection | Templates, hardware | LLM model |

Both standards use the same extraction pipeline, ground truth fixtures, and validation tools. When a PR changes both templates and the model simultaneously, both standards apply and both reports are required.

---

## 11. Planned Automation

The following steps are currently manual and are tracked under epic #97 (Quality Evaluation Pipeline):

| Step | Current status | Tracking |
|---|---|---|
| LLM-as-judge scoring | Manual | #97 |
| Composite score calculation | Manual (formula in §3) | #97 |
| Dedup blocking gate enforcement | Semi-automated (`dedup_audit.py` reports; blocking is manual) | #97 |
| Evaluation report generation | Manual template (§7) | #97 |
| Model evaluation task submission | Planned — submit eval as orchestrator task | #430 |
