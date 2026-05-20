# A/B Testing Standard

## When Required

A/B testing is required for any PR that modifies extraction behavior:
- Files under `templates/extraction/`
- `tools/semantic_extraction.py`
- `tools/bootstrap_session.py`

## Requirements

1. **Minimum 3 paired runs** per variant (both A and B valid in same run)
2. **Same model, same turns, same hardware** for both variants
3. **Results posted as PR comment** with entity count comparison

## Running A/B Tests

Use the orchestrator's AB test adapter or run manually:

```bash
python saas/orchestrator/scripts/ab_test.py
```

Environment variables:
- `AB_TEST_REPO_A` — path to main branch worktree
- `AB_TEST_REPO_B` — path to variant branch worktree
- `AB_TEST_BASE_URL_A` / `AB_TEST_BASE_URL_B` — LLM endpoint URLs
- `AB_TEST_RUNS` — number of runs per variant (default: 3)

## Acceptance Criteria

- No variant produces 0 entities (infra failure)
- Intra-variant divergence < 20%
- Results clearly demonstrate the PR's claimed improvement
