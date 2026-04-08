# Copilot Prompt Template — Next-Move Analysis

Use this prompt to generate a next-move analysis and prompt candidate suggestions
after one or more new turns have been ingested.

---

## When to use
After running `python tools/update_state.py --session sessions/{session_id}`, or after
asking Copilot to ingest and update state for the latest turn.

## Required context files to load first
- `sessions/{session_id}/derived/state.json`
- `sessions/{session_id}/derived/evidence.json`
- `sessions/{session_id}/derived/objectives.json`
- `framework/strategy/heuristics.md`
- `framework/strategy/manipulation-patterns.md`
- `framework/strategy/risk-model.md`
- `framework/strategy/hint-interpretation.md`
- `framework/dm-profile/dm-profile.json`

---

## Prompt

```
Generate a next-move analysis for sessions/{session_id} as of turn-{NNN}.

Use the current state.json, evidence.json, objectives.json, and framework strategy files.

Produce:

1. sessions/{session_id}/derived/next-move-analysis.md
   Answer these questions in order:
   a. What changed since the last analysis?
   b. What is known (explicit_evidence) vs. inferred (inference)?
   c. What evidence is classified as dm_bait? Why?
   d. What opportunities are currently available?
   e. What risks have increased?
   f. Which objectives (from objectives.json) are most affected?

2. sessions/{session_id}/derived/prompt-candidates.json
   Generate at least 3 candidate prompts with different strategies.
   Default mode: desired_outcome.

   Each candidate must include:
   - id
   - recommendation_mode (desired_outcome / roleplay_consistent / all_options)
   - style (safe / probing / direct / diplomatic / aggressive / deceptive / exploratory)
   - proposed_prompt (exact text the player would send)
   - rationale
   - expected_upside
   - risk
   - objective_refs

   Include at minimum:
   - One safe / information-gathering option
   - One probing / investigative option
   - One outcome-seeking / direct option

Constraints:
- Never present inference as fact in the analysis.
- Cite source_turns for every evidence claim.
- Align prompt candidates with active objectives where possible.
- Adjust risk ratings based on dm-profile adversariality level.
```

---

## Modes
- **desired_outcome** (default): optimise for the player's declared objectives
- **roleplay_consistent**: prioritise staying in character, even at some strategic cost
- **all_options**: return a broader set across all strategy styles

Replace `desired_outcome` in the prompt if a different mode is needed.
