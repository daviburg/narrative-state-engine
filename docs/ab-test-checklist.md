# A/B Test Checklist for Template Changes

Embed this checklist in `.prompt.md` files for PRs that modify `templates/extraction/*.md`.
Full standard: [docs/ab-test-standard.md](ab-test-standard.md).

---

## Pre-Flight

- [ ] Both LLM servers reachable (`curl http://localhost:8000/v1/models`, `:8001`)
- [ ] On `main` branch, pulled latest
- [ ] `config/llm.json` unchanged between A and B runs
- [ ] Output directories will be created per-run: `framework-ab-a-run{1,2,3}`, `framework-ab-b-run{1,2,3}`

## Run A (Baseline — main branch)

```bash
# Run 1:
python tools/bootstrap_session.py \
    --session sessions/session-import \
    --file sessions/session-import/raw/full-transcript.md \
    --extract --framework framework-ab-a-run1 --max-turns 30 --overwrite \
    --base-url http://localhost:8000/v1

# Run 2:
python tools/bootstrap_session.py \
    --session sessions/session-import \
    --file sessions/session-import/raw/full-transcript.md \
    --extract --framework framework-ab-a-run2 --max-turns 30 --overwrite \
    --base-url http://localhost:8000/v1

# Run 3:
python tools/bootstrap_session.py \
    --session sessions/session-import \
    --file sessions/session-import/raw/full-transcript.md \
    --extract --framework framework-ab-a-run3 --max-turns 30 --overwrite \
    --base-url http://localhost:8000/v1
```

- [ ] Run A completed ×3 (minimum). Outputs in `framework-ab-a-run{1,2,3}/catalogs`

## Run B (Candidate — PR branch)

```bash
git checkout <pr-branch>

# Run 1:
python tools/bootstrap_session.py \
    --session sessions/session-import \
    --file sessions/session-import/raw/full-transcript.md \
    --extract --framework framework-ab-b-run1 --max-turns 30 --overwrite \
    --base-url http://localhost:8001/v1

# Run 2:
python tools/bootstrap_session.py \
    --session sessions/session-import \
    --file sessions/session-import/raw/full-transcript.md \
    --extract --framework framework-ab-b-run2 --max-turns 30 --overwrite \
    --base-url http://localhost:8001/v1

# Run 3:
python tools/bootstrap_session.py \
    --session sessions/session-import \
    --file sessions/session-import/raw/full-transcript.md \
    --extract --framework framework-ab-b-run3 --max-turns 30 --overwrite \
    --base-url http://localhost:8001/v1
```

- [ ] Run B completed ×3 (minimum). Outputs in `framework-ab-b-run{1,2,3}/catalogs`

## Validation (each run)

```bash
# Schema validation (always required):
python tools/validate.py

# Ground truth validation (full-session runs only — requires full-session fixture schema):
python tools/validate_extraction.py \
    --catalog-dir framework-ab-{a,b}-run{N}/catalogs \
    --ground-truth tests/fixtures/extraction-ground-truth-full-session.json
```

> **Note:** `validate_extraction.py` requires the full-session fixture (`extraction-ground-truth-full-session.json`) which has a different schema than the turns-1-30 fixture. For turns 1–30 runs, use `validate.py` for schema checks and manually review against `extraction-ground-truth-turns-1-30.json`.

- [ ] `validate_extraction.py` exits 0 for all B runs
- [ ] `validate.py` reports 0 schema violations for all B runs

## Metrics Collection

- [ ] Entity counts by type (characters, locations, items, factions, events)
- [ ] Relationship count (total)
- [ ] Wall-clock time per run
- [ ] Mean ± σ computed for all metrics across runs

## Thresholds — Automated (Pass/Fail)

| Metric | PASS | WARN | BLOCK |
|---|---|---|---|
| Entity count **loss** | Δ ≤ 5% loss | 5–15% loss | > 15% loss |
| Entity count **gain** | Δ ≤ 10% gain | 10–20% gain | > 20% gain (hallucination signal) |
| Single type count **loss** | Δ ≤ 10% loss | 10–20% loss | > 20% loss |
| Single type count **gain** | Δ ≤ 15% gain | 15–25% gain | > 25% gain (hallucination signal) |
| Relationship count **loss** | Δ ≤ 10% loss | 10–20% loss | > 20% loss |
| Relationship count **gain** | Δ ≤ 15% gain | 15–25% gain | > 25% gain (hallucination signal) |
| Performance regression | Δ ≤ +10% time | +10–20% time | > +20% time |
| Performance improvement | Always PASS | — | — |
| Schema validity | 100% | ≥ 95% | < 95% |
| Ground truth validation | 0 FAILs | — | Any FAIL |

## Manual Review (Recommended)

> ⚠️ These checks require human review. They do NOT block merge but SHOULD be performed for major template changes.

| Metric | PASS | WARN | BLOCK |
|---|---|---|---|
| Attribute completeness | ≥ 90% | 75–89% | < 75% |
| Hallucination rate | 0% | ≤ 5% | > 5% |

## PR Report

- [ ] A/B Test Results section added to PR body (see template in `docs/ab-test-standard.md` §5.1)
- [ ] All tables filled with mean ± σ values
- [ ] Zero BLOCK statuses
- [ ] All WARN statuses have written justification
- [ ] Verdict checkboxes completed

## Exemptions

No A/B test required for:
- Comment/formatting-only changes to templates
- Changes to `dm-profile-analyzer.md`
- New template files not yet wired into extraction pipeline

State exemption reason under "A/B Test Exemption" heading in PR description.
