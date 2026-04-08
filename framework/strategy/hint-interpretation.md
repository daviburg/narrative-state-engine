# Hint Interpretation

This document provides patterns for decoding DM hints, planted details, and narrative signals.

Cross-reference: [heuristics.md](heuristics.md) · [risk-model.md](risk-model.md) · [manipulation-patterns.md](manipulation-patterns.md)

---

## Signal vs. Noise

Not every DM detail is a meaningful hint. Apply this filter first:

| High signal | Low signal |
|---|---|
| Detail repeated across multiple turns | Mentioned once in passing |
| Detail that requires no direct player action | Background colour with no hook |
| Detail that is surprising given context | Expected setting detail |
| Detail the DM emphasises (length, timing, position in response) | Buried at the end of a long paragraph |
| Detail the DM volunteers without being asked | Detail only revealed when directly questioned |

---

## Hint Classifications

### Genuine lore drop
The DM is rewarding player curiosity or in-world engagement with authentic worldbuilding. These are safe to act on.

Signals: Consistent with established world-state, not artificially urgent, no visible payoff pressure.

### Opportunity hook
The DM is opening a narrative door. Not necessarily bait — may be a legitimate path.

Signals: A new NPC with specific skills, a door left ajar, an overheard conversation.

Action: Probe before committing. Gather information about the hook before walking through.

### Railroading signal
The DM is directing the player toward a predetermined path by making one option conspicuously easier.

Signals: One option described in much more detail than others, other paths described as hazardous without evidence, NPCs who steer without being asked.

Action: Name the rail in next-move analysis. Decide whether to follow it (with eyes open) or test its boundaries.

### DM bait
A lure designed to exploit player assumptions or desires. Following it serves the DM's narrative agenda rather than the player's objectives.

Signals: Artificially convenient timing, overemphasis on a single detail, suspicious alignment with a stated player goal, earlier pattern of similar lures.

Action: Classify as `dm_bait` in evidence.json. Do not act on it without cross-verification from a different source.

---

## Timing and Emphasis Patterns

| Pattern | Interpretation |
|---|---|
| DM volunteers information the player did not ask for | Possibly important; log, probe before acting |
| DM deflects a direct question with vague alternatives | The deflected topic is probably significant |
| DM describes something in unusual physical or sensory detail | Planted detail; likely to matter later |
| DM introduces urgency or time pressure unprompted | Possible railroading or bait; pause and assess |
| NPC repeats the same message in slightly different words | Scripted narrative emphasis; treat as high-signal |
| DM response is unusually short after a probing question | Either the topic is sensitive or the question was off-path |

---

## Cross-Verification Protocol

Before acting on an inference or possible hint:

1. Check whether any other turn in the transcript supports the same conclusion independently.
2. Test with a low-cost, low-commitment probe prompt before committing to an action.
3. If classified as `dm_bait`, require two independent corroborating signals before treating it as genuine.
4. If still uncertain after probing, classify as `player_hypothesis` and continue gathering evidence.

---

## Ambiguous Language Taxonomy

| Language pattern | Likely meaning |
|---|---|
| "You could…" | Genuine option, likely low risk |
| "You might want to…" | Mild steering; not necessarily bait |
| "It would be unwise to…" | Strong warning; take seriously unless DM is known to bluff |
| "As far as you can tell…" | DM is signalling information uncertainty; do not treat as confirmed |
| "Somehow…" | DM is glossing over a mechanic; probe it |
| "For some reason…" | DM is hiding a causal explanation; investigate |
