# Extraction Run B Status (2026-05-12)

## Run Details
- **Output dir**: `framework-local/ab-test/b70-full-b`
- **Log**: `framework-local/ab-test/b70-full-b.log` (stdout), `.log.err` (stderr)
- **PID file**: `framework-local/ab-test/b70-full-b.pid`
- **Stalled PID**: 41916 (wrapper 5284) — detached, will survive session exit
- **Last turn processed**: turn-122 (of 175 target)
- **Started**: 2026-05-12 06:38:40
- **Stalled at**: ~10:25 AM — B70 server went down

## Resume Instructions
1. Kill stalled process: `Stop-Process -Id 41916 -Force; Stop-Process -Id 5284 -Force`
2. Restart B70 servers: `ssh arclight` → restart ov_serve.py on 8000/8001
3. Flush: `POST http://192.168.10.169:8000/admin/flush` and `:8001/admin/flush`
4. Resume: `python tools/bootstrap_session.py --session sessions/session-import --file sessions/session-import/raw/full-transcript.md --framework framework-local/ab-test/b70-full-b --start-turn 123 --max-turns 175 --segment-size 0`
5. (Do NOT use `--overwrite` on resume — it would wipe existing catalogs)

## Entity Counts at Turn 122

| Type | Run B | Baseline A (175) |
|------|-------|-------------------|
| Characters | 19 | 23 |
| Locations | 13 | 19 |
| Items | 24 | 43 |
| Factions | 3 | 7 |
| **Total** | **59** | **92** |

## Feature Activations
- Concept-prefix filter: 20
- Within-turn dedup: 4 
- DEDUP-AUDIT: 3 (2 merges at turn 50: loc-camp+loc-camp-edge, item-dark-fibrous-material+item-material)
- Name guard: 1 (correctly blocked char-youthful-hunter)
- Possessive filter: 0
- Stale sweep: 0 (end-of-run)
- Entity refresh: 0

## Errors
- turn-093: fallback timeout (recovered via JSON repair)
- turn-115: entity discovery failed (fallback model not found)
- Fallback config references qwen3.5-9b-q4_k_m but only qwen3-8b-int4-ov is available

## After Completion
1. Compare final B vs A entity counts
2. Generate wiki: `python tools/generate_wiki.py --framework framework-local/ab-test/b70-full-b`
3. Schema validation: `python tools/validate.py`
4. Quality assessment of new features' impact
