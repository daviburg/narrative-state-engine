# Copilot Prompt Template — Resume Analysis

Use this prompt when returning to a session after a break and needing a fast, context-loaded summary
before continuing. Optimised for token efficiency — loads catalogs first, deep context only if needed.

---

## When to use
At the start of a VS Code Copilot chat session, or when assigning a GitHub issue after a break.

## Required context files to load first (in this order)
1. `framework/catalogs/characters.json`
2. `framework/catalogs/locations.json`
3. `framework/catalogs/plot-threads.json`
4. `framework/objectives/objectives.json`
5. `sessions/{session_id}/derived/state.json`
6. `sessions/{session_id}/derived/turn-summary.md`

Load full transcript or evidence.json **only if** the above is insufficient to answer the question.

---

## Prompt

```
I am resuming session {session_id}. The last turn was turn-{NNN}.

Using the catalog files, state.json, and turn-summary.md as primary context:

1. Summarise where the player currently is:
   - Location
   - Active relationships and NPC dispositions
   - Physical state and inventory notes (if known)

2. List the 3 most important active objectives (from objectives.json), in priority order.

3. Identify the top 2–3 open risks or unresolved questions that the player should address next.

4. Identify any dm_bait evidence that has not yet been resolved.

5. Suggest 2–3 possible next player prompts, briefly, to get back into the session.

Keep the summary concise. If any of the above requires information not in the catalog or state files,
note the gap rather than inventing an answer.
```

---

## Expected outputs
- Verbal summary (in-chat, not written to a file)
- Optionally: 2–3 draft prompt candidates to paste into the session
