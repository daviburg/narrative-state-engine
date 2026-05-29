# A/B Testing

## When Required

A/B testing is required for any PR that modifies extraction behavior:
- Files under `templates/extraction/`
- `tools/semantic_extraction.py`
- `tools/bootstrap_session.py`

## Requirements

1. **Runs per variant:** 1 run suffices at the temperature 0 default (deterministic); **minimum 3 paired runs** per variant only at temperature > 0 (see ab-test-standard.md §1.2)
2. **Same model, same turns, same hardware** for both variants
3. **Results posted as PR comment** with entity count comparison

## Overview

A/B tests compare extraction quality between two code variants (e.g. a feature
branch vs. `main`) by running `bootstrap_session.py` N times per variant and
measuring entity counts. Results are tracked on the orchestrator dashboard.

An A/B test is submitted as an `ab_test` task to the orchestrator. The
orchestrator adapter:

1. Runs `bootstrap_session.py` for each variant × run combination
2. Counts extracted entities per run
3. Flags **zero-entity runs** as immediate failures
4. Computes inter-variant and intra-variant divergence
5. Reports progress and a final comparison summary to the dashboard

## Prerequisites

- Both variant repos checked out locally (or on the extraction machine)
- LLM server(s) running and accessible (one URL per variant, or a shared URL)
- Orchestrator service running (`python -m saas.orchestrator run`)

## Submitting an A/B Test

Use `tools/submit_ab_test.py` to generate a task definition and optionally
submit it to the orchestrator.

### Minimal example

```bash
python tools/submit_ab_test.py \
    --pr 399 \
    --variant-a main \
    --variant-b feat/my-prompt-change \
    --repo-a /path/to/repo-main \
    --repo-b /path/to/repo-feat
```

This writes `ab-test-pr399.json` and prints the submit command:

```
To submit:
  python -m saas.orchestrator submit ab-test-pr399.json
```

### Submit immediately

```bash
python tools/submit_ab_test.py \
    --pr 399 \
    --variant-a main \
    --variant-b feat/my-prompt-change \
    --repo-a /path/to/repo-main \
    --repo-b /path/to/repo-feat \
    --submit
```

### Full options

```
--pr INT              PR number being tested (required)
--variant-a BRANCH    Branch name for variant A / baseline (required)
--variant-b BRANCH    Branch name for variant B / candidate (required)
--repo-a PATH         Filesystem path to the variant A repo checkout
--repo-b PATH         Filesystem path to the variant B repo checkout
--runs INT            Runs per variant (default: orchestrator config, typically 3; use 1 at temperature 0)
--turns RANGE         Turn range to process, e.g. "1-30" or "30"
--base-url-a URL      LLM endpoint for variant A (default: http://localhost:8080/v1)
--base-url-b URL      LLM endpoint for variant B (default: http://localhost:8081/v1)
--session PATH        Session directory (e.g. sessions/session-import)
--transcript PATH     Path to transcript file
--output-dir PATH     Directory for per-run output files
--task-id ID          Override auto-generated task ID
--timeout SECONDS     Task timeout (default: 7200)
--divergence-threshold FLOAT
                      Max inter-variant divergence % before flagging (default: 20.0)
--output FILE         Write task JSON here (default: ab-test-pr<N>.json)
--submit              Submit immediately via the orchestrator CLI
--orchestrator-config FILE
                      Path to orchestrator config.json (only with --submit)
```

## Task Definition Format

The generated JSON follows the orchestrator's `TaskDefinition` schema:

```json
{
  "id": "ab-test-pr399-a1b2c3",
  "name": "A/B test PR #399: main vs feat/my-change",
  "adapter": "ab_test",
  "timeout": 7200,
  "metadata": {
    "pr_number": 399,
    "variant_a_branch": "main",
    "variant_b_branch": "feat/my-change",
    "repo_a": "/path/to/repo-main",
    "repo_b": "/path/to/repo-feat",
    "runs_per_variant": 3,
    "turns": "1-30"
  },
  "success_criteria": [
    { "type": "exit_code", "expected": 0 },
    { "type": "entity_count", "expected": 1 },
    { "type": "variant_divergence", "expected": 20.0 }
  ]
}
```

You can submit this directly without the helper script:

```bash
python -m saas.orchestrator submit ab-test-pr399.json
```

## Monitoring Progress

Check task status on the dashboard or via the CLI:

```bash
# Table view
python -m saas.orchestrator status

# JSON output
python -m saas.orchestrator status --json
```

The dashboard at `arclight:8080` shows per-variant, per-run progress with live
entity counts. Zero-entity runs are flagged as errors within 60 seconds of
completion.

## Result Interpretation

After the task completes, the log output contains:

```
[RESULT] variant=a run=1 entities=142 exit_code=0 duration=312.5
[RESULT] variant=a run=2 entities=145 exit_code=0 duration=308.2
[RESULT] variant=b run=1 entities=138 exit_code=0 duration=320.1
[RESULT] variant=b run=2 entities=141 exit_code=0 duration=315.7
[SUMMARY] pr=399 variant_a_mean=143.5 variant_b_mean=139.5 divergence_pct=2.8
```

| Metric | Description |
|--------|-------------|
| `variant_a_mean` / `variant_b_mean` | Average entity count across runs for each variant |
| `divergence_pct` | `abs(A_mean - B_mean) / max(A_mean, B_mean) × 100` |
| `max_intra_variant_divergence_pct` | Max spread within the same variant (consistency check) |

The task is marked **failed** if:
- Any run produces 0 entities
- `exit_code != 0` for any run
- Inter-variant divergence exceeds `--divergence-threshold` (default 20 %)

## Log Files

Each run writes an `output.log` under the configured `output_dir`:

```
<output_dir>/
  a/run_1/output.log
  a/run_1/framework/catalogs/...
  a/run_2/output.log
  b/run_1/output.log
  ...
```

A combined `master.log` is written to the orchestrator's log directory and
parsed for the structured `[RESULT]` / `[SUMMARY]` lines.

## Acceptance Criteria

- No variant produces 0 entities (infra failure)
- Intra-variant divergence < 20 %
- Inter-variant divergence within `--divergence-threshold` (default 20 %)
- Results clearly demonstrate the PR's claimed improvement
