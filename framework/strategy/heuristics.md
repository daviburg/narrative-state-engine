# Strategy Heuristics

These heuristics guide the player-assistant when generating next-move analysis and prompt candidates.

---

## General Principles

1. **Gather before committing.** When information is incomplete, prefer information-gathering actions over commitment.
2. **Test assumptions low-cost.** Ask probing questions before taking irreversible actions.
3. **Track what the DM emphasizes.** Repeated mentions of a detail may signal importance or a planted hook.
4. **Be skeptical of easy wins.** If something seems too convenient, check for dm_bait classification.
5. **Maintain cover.** Avoid revealing the player's full objective or information to NPCs unless tactically useful.
6. **Prioritize open questions.** Focus analysis on unresolved plot thread questions before exploring tangents.

---

## Evidence Evaluation

- Weight `explicit_evidence` highest.
- Treat `inference` as a working hypothesis — not confirmed until corroborated.
- Flag `dm_bait` entries for extra scrutiny before acting on them.
- Revisit `player_hypothesis` after 3+ turns without corroboration.

---

## Objective Prioritization

- Short-term tactical objectives should always be evaluated in the context of long-term strategic objectives.
- If a tactical action risks a strategic objective, flag the conflict in next-move-analysis.
- Prefer prompt candidates that advance both a tactical and strategic objective simultaneously.

---

## Prompt Style Guidelines

| Style | When to use |
|---|---|
| `safe` | When stakes are high and information is low |
| `probing` | When gathering information is the priority |
| `exploratory` | When in a new area or new situation |
| `direct` | When an objective is clear and the path is established |
| `diplomatic` | When NPC relationships are key |
| `aggressive` | Only when prepared for consequences |
| `deceptive` | High risk; use only when other options are exhausted |
