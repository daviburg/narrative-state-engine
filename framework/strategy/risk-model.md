# Risk Model

This document defines how to classify and reason about narrative risk when selecting player actions.

Cross-reference: [heuristics.md](heuristics.md) · [manipulation-patterns.md](manipulation-patterns.md) · [hint-interpretation.md](hint-interpretation.md)

---

## Risk Dimensions

Every candidate action should be evaluated across these dimensions before selecting a prompt:

| Dimension | Question |
|---|---|
| **Reversibility** | Can the outcome be undone if it goes wrong? |
| **Commitment cost** | Does this lock in a path or foreclose options? |
| **Information exposure** | Does this reveal the player's intentions, knowledge, or strategy? |
| **DM reaction likelihood** | How predictably will the DM reward vs. penalise this action? |
| **Cascade risk** | Could this trigger unintended consequences in other threads? |

---

## Risk Classification

### Low risk
- Information-gathering actions
- Observational or passive prompts
- Actions that are fully reversible in-world
- Actions supported by explicit DM evidence

### Medium risk
- Actions that partially reveal player intent
- Commitments the character could narratively walk back
- Actions based primarily on inference rather than explicit evidence

### High risk
- Irreversible actions (major confrontations, spending resources, destroying information)
- Actions that directly test an unconfirmed inference
- Actions that expose the player's long-term objective to an NPC
- Acting on something classified as `dm_bait` without cross-verification

---

## DM Adversariality Adjustment

Risk ratings shift based on the inferred DM adversariality level (from `framework/dm-profile/dm-profile.json`):

| DM adversariality | Effect on risk model |
|---|---|
| Low (permissive) | Medium-risk actions can be treated as low-risk; DM is likely to reward initiative |
| Medium (balanced) | Use standard risk classifications |
| High (adversarial) | Upgrade all risk ratings by one level; verify every assumption before committing |

---

## Commitment Cost Matrix

Before taking an action, assess:

1. **Tactical cost** — does this use up an encounter resource (time, goodwill, cover)?
2. **Strategic cost** — does this change an NPC's long-term disposition or a plot thread's trajectory?
3. **Information cost** — what does the DM now know about the player's plan?

Prefer actions with low tactical + low information cost when the strategic benefit is uncertain.

---

## Risk Escalation Patterns

Watch for these patterns that indicate rising risk:

- DM responses become shorter and more clipped (losing narrative richness)
- An NPC's tone shifts from neutral to guarded
- The DM introduces time pressure or urgency unprompted
- A seemingly helpful NPC appears at exactly the right moment
- Two separate plot threads suddenly converge (possible forced encounter setup)

When these appear, reduce aggression and increase information-gathering before committing.
