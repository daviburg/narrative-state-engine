# Token Degeneracy & Bounding Plan — Consolidated Analysis (for Adversarial Review)

**Status:** Analysis + plan. Authored for **adversarial review**. Its explicit purpose is to be
attacked: if a claim below is wrong, the plan that depends on it should be killed before any GPU
spend. Read **§6 (Caveats / Weak Points)** first if you are the reviewer — it lists where this
analysis is least defensible.

**Epic:** [#477](https://github.com/daviburg/narrative-state-engine/issues/477) — Stabilize per-turn
extraction token demand at quality parity.
**Sub-issues this plan sequences:** [#494](https://github.com/daviburg/narrative-state-engine/issues/494)
(lever b, relevance-scoped detail), [#495](https://github.com/daviburg/narrative-state-engine/issues/495)
(lever c-i, cap+age PC web), [#496](https://github.com/daviburg/narrative-state-engine/issues/496)
(lever c-ii, checkpoint reset).
**Companion design (current truth):** `docs/design-bounded-context-relevance-reset.md`.

**Grounding:** `origin/main` @ `cf22a27` (`feat: relevance-scoped entity_detail selection (S1a
recover-only, A2a lever b) (#499)`), fetched 2026-06-13. All code citations are line numbers in that
tree. All token/log numbers are from the three L2 control runs (`l2-control-r1/r2/r3`) under
`/data/nse-agent/l2-ab/` on the `arclight` host, the per-turn `framework/extraction-log.jsonl`, and
the `llama-vk*.log` server-timing logs, plus the round-53 char-player semantic inspection. Where a
number could not be measured directly (raw prompts/completions are **not** logged anywhere — see §6),
this doc says so explicitly and labels the value a **proxy**.

> **Honesty contract.** Conclusions in §1–§5 are stated at the confidence the evidence supports.
> §6 enumerates the load-bearing assumptions that are **unverified** and the single instrumentation
> gap that, if it breaks the wrong way, collapses the §3 LEVER-1 savings estimate by ~10x. The plan
> in §7 is sequenced so the cheapest step (raw-IO capture, §7.1) resolves that gap **before** any
> lever is funded.

---

## 0. TL;DR for the reviewer

1. **Two different axes are being conflated in casual discussion and we separate them here.** Token
   *count* growth is an **INPUT** phenomenon (context-window risk). GPU *cost* is an **OUTPUT/decode**
   phenomenon (87% of wall-clock). They grow for different reasons and need different levers (§1).
2. **There is real, content-verified degeneracy** in the char-player (PC) `entity_detail` path — not
   merely rising counts. ~half the injected PC relationship web is stale cruft that is never demoted,
   and the model re-emits ~80% of the entity unchanged every call (§2). The output figure is a
   **proxy** (§6).
3. **Two levers follow from the content**, not from blind size reduction: **DELTA-OUTPUT** (stop
   re-emitting unchanged fields; attacks decode = cost) and **RELATIONSHIP-DEMOTION** (age out stale
   PC bonds; attacks the injected web = context window) (§3).
4. **Cost (A) is a prerequisite to affordably test window ideas (B)**: each B experiment costs ~3
   GPU-days, so cutting decode first makes every future B run cheaper. Sequence delta-output first (§4).
5. **A/B runs can be truncated early** if the degeneracy signal already clears the noise floor. We
   computed where: delta-output resolves at ~turn 216 (−53% GPU vs full); relationship-demotion is
   **not** truncatable on the total-token metric and instead needs a **per-entity instrumentation**
   change to become cheap (§5).
6. **The whole chain rests on one deeply-inspected entity + aggregate stats and on a proxy for output**
   (§6). The plan's first funded step is measurement, not optimization.

---

## 1. The reconciliation: input grows (cheap), output is flat (expensive)

The starting tension: people see "tokens grow unbounded" and "GPU time grows 4x" and assume they are
the same problem. They are not. The reconciliation:

### 1.1 INPUT / prompt tokens grow, ~linearly, with no plateau

- `entity_detail` `raw_input` ≈ **5,252 tok @ turn 1 → ≈ 42,312 tok @ turn 344**, slope ≈ **64
  tok/turn** (per `extraction-log.jsonl`, confirmed across `l2-control-r1/r2/r3`).
- Per-turn **total** input ≈ **15.9k → 51.9k tok** over the same span.
- This is the context-window risk: at 300+ turns the prompt approaches the model's logical window.
- **Note:** epic #477's earlier headline slope was `15182 + 88.6·turn_index` (total) and "~151 tok/turn
  late-window". Those and the ~64 tok/turn `entity_detail`-only slope here are consistent — different
  scopes (total vs. one phase) and different fit windows. We use the per-phase `entity_detail` slope
  because that phase is the dominant and the one the levers touch.

### 1.2 OUTPUT / decode is FLAT

- `entity_detail` output ≈ **388 → 447 tok/call** across the whole run — it **plateaus**, it does not
  grow with session depth.
- Per-turn output is **bounded by the calls/turn cap**: `_MAX_DETAIL_ENTITIES_PER_TURN = 6`
  ([tools/semantic_extraction.py L1163](../tools/semantic_extraction.py#L1163)), enforced in the
  detail-cap selection block at
  [tools/semantic_extraction.py L4049-L4071](../tools/semantic_extraction.py#L4049-L4071).

### 1.3 GPU wall-clock is ~87% DECODE / ~13% prefill, at every stage

- The `llama-vk*.log` server timings show **86.8% decode / 13.2% prefill** at every measured stage of
  the run (early, mid, late). Decode is ≈ **24x costlier per token** than prefill on this hardware.
- The **prefix cache absorbs most of the input**: median *uncached* prefill ≈ **1,030 tok** even when
  the logical prompt is ~32k tok. So input growth is largely *free* at the GPU — it is re-sent but
  re-used from cache.
- Per-turn wall-clock grows ≈ **4x (51s → 205s)**. The driver is **more decode-heavy CALLS
  serializing on 2 GPUs**, NOT input-token growth. (Calls/turn rise 3.25 → 5.93 toward the cap of 6.)

### 1.4 The implication

> Token **COUNT** growth = **INPUT** = context-window risk.
> GPU **COST** = **OUTPUT / decode**.
> They are **different axes** and must be attacked by different levers. A lever that shrinks input
> tokens (compaction) barely touches GPU cost (prefix cache already reuses the input); a lever that
> shrinks output (delta) barely touches the context-window curve. This is the central structural
> claim of the whole plan, and it matches epic #477's "COST FACT" (fact 6).

---

## 2. The semantic inspection (round 53): REAL degeneracy, content-verified

Counts alone do not prove degeneracy — they could be valid growth. So we inspected the **content** of
the PC (`char-player`) `entity_detail` call at **turn 10 vs turn 344**. The prompt was
**RECONSTRUCTED** by replaying `format_detail_prompt` /
[`_format_prior_entity_context` (L1922)](../tools/semantic_extraction.py#L1922) against the catalog
state at each turn, because the **raw prompt and completion are NOT logged anywhere** — this is the
tooling gap that §6 and §7.1 address.

### 2.1 INPUT side — the late PC call is dominated by a half-stale relationship web

Late (turn-344) PC `entity_detail` call ≈ **18,690 tok**, decomposed:

| Component | Tokens | Share | Growth behavior |
|---|---:|---:|---|
| Relationship web | 14,579 | **78%** | **grows** with arc |
| Template (fixed) | 2,175 | 12% | flat |
| identity + status + volatile + stable | ~4,100* | ~10% | ~flat |

\*The identity/status/volatile/stable block "barely grows" — it is essentially constant per-entity.

The relationship web is **not uniformly valid**:

- Of **105 relationships**, **48 are STALE** (>100 turns since last update), and **ALL are still
  marked "active"** — never demoted. Verbatim examples from the inspection:
  - `located_at loc-icy-path` — last updated **turn-4**, age **340** turns, still active @344.
  - `captive_of char-unknown-figures` — narratively resolved ~**turn-29**, still active @344.
  - `received_broth_from …` — a one-off interaction, age **313**, still active.
- ≈ **half the web (~7,000 tok) is CRUFT**; the other half (current bonds — gorok / renn / finn) is
  **VALID**.

> Conclusion: the injected input is **NOT uniformly valid**. About half of the biggest input
> component is stale relationships that were never demoted from "active". This is the degeneracy on
> the input axis, and it is **specific and addressable** (demote stale bonds), not a blanket "shrink
> the prompt".

This is consistent with the code: L1 type-tiering
([`_apply_pc_rel_type_tier`, L1776](../tools/semantic_extraction.py#L1776)) caps the *volatile tail*
at `pc_rel_volatile_tail_cap = 10`
([`_PC_REL_VOLATILE_TAIL_CAP_DEFAULT = 10`, L1229](../tools/semantic_extraction.py#L1229)) but leaves
**permanent-bond types UNCAPPED by design**, and **nothing ages a permanent bond out of "active"**.
That is exactly the gap #495 (lever c-i) targets.

### 2.2 OUTPUT side — ~80% restatement (PROXY — see §6)

Using the **parsed catalog as a proxy** for the model's completion (raw completions are not logged):

- The model appears to **re-emit the WHOLE entity every call**: ≈ **80% RESTATEMENT**.
- `current_status` + `volatile_state` have been **FROZEN since turn-195** (149 turns) yet appear
  re-emitted every call.
- **11 of 14** `stable_attributes` are unchanged for **90–220 turns**.
- Only ≈ **15–20%** of each completion is a genuinely new delta.

### 2.3 OUTPUT side — the PC stable-attribute allowlist BUG (verified on-disk; re-emit is proxy)

- The PC on-disk catalog stores **14 stable_attribute keys**, including `role`, `condition`, `skills`,
  `duty` — keys the `entity-detail.md` template **explicitly forbids** for `char-player`. The template
  allowlist is `species / race / class / aliases`
  ([templates/extraction/entity-detail.md L57](../templates/extraction/entity-detail.md#L57)), which
  must match `_PC_KEY_STABLE_ATTRS = {"species","race","class","aliases"}`
  ([tools/semantic_extraction.py L1142](../tools/semantic_extraction.py#L1142)).
- **The injection path DOES enforce the allowlist** — `_format_prior_entity_context` trims PC stable
  attrs to `_PC_KEY_STABLE_ATTRS` at
  [L1979](../tools/semantic_extraction.py#L1979). **But the MERGE path does NOT.** The catalog merge
  deep-merges every key the model returns with no PC allowlist guard
  ([tools/catalog_merger.py L1196-L1206](../tools/catalog_merger.py#L1196-L1206)). So illegal keys the
  model emits get **persisted to disk** and never cleaned. That is why the on-disk PC entity has 14
  keys despite the injection-time trim — a verified asymmetry.
- **Estimated ~600 tok of illegal bloat** is associated with these keys.

> **Important honesty flag (expanded in §6):** the injection-time trim means the *injected prior* does
> NOT contain the illegal keys, so a naive reading says the model can't be "re-emitting" what it never
> sees. The on-disk persistence is verified; the *per-call re-emission* of that bloat is **inferred
> from the parsed catalog proxy**, not from a captured completion. The bug (no allowlist at merge) is
> real and worth fixing on correctness grounds regardless; its *token* impact is part of the
> unverified output thesis.

---

## 3. The two levers (content-justified, not blind size reduction)

The point of §2 is that the levers are chosen **because the content shows a specific defect**, not
because "the prompt is big".

### LEVER 1 — DELTA-OUTPUT (attacks the 87% DECODE = COST, axis A)

Emit **only CHANGED fields** instead of restating the whole entity, and **enforce the PC stable-attr
allowlist at merge** (fix the §2.3 bug). Rationale: if ~80% of output is restatement (§2.2), the
decode lever has a **real target**, and decode is 87% of GPU cost (§1.3). This is the cost/(A) win.

### LEVER 2 — RELATIONSHIP-DEMOTION (attacks the injected web = CONTEXT WINDOW, axis B; this is #495)

Add **recency/status-aware demotion** of PC relationships: a PC relationship cap plus **dormancy
demotion** of bonds that are stale-but-still-"active" (the §2.1 defect). Estimated effect on the late
PC call: ≈ **18.7k → 5–6k tok**. This is the context-window/(B) win.

### NOT levers (do not touch)

- The **fixed template** (2,175 tok) and the **current factual fields** (identity / status / volatile
  / stable *current values*) are **valid and ~flat** (§2.1 table). Compacting them is a one-time
  constant downshift that does not change the slope (this is the A1a #492 "dead premise" result in
  epic #477). To bound the window, **RESET** them periodically (lever c-ii / #496), do not compress.

---

## 4. The (A)–(B) unification (strategic insight)

- Each (B) context-window experiment costs ≈ **3 GPU-days** (≥3 paired runs/variant per #488, at
  ~62 GPU-h for a full 344-turn A/B — see §5).
- (A) DELTA-OUTPUT cuts decode, which is the dominant wall-clock term (§1.3), so it makes **every
  future (B) experiment cheaper**.
- Therefore **(A) enables (B)**, and the sequence is: **delta-output FIRST**, then the window levers.

This is the same conclusion epic #477 reached for lever (b) ("the DUAL lever: it bounds the curve AND
cheapens every future A/B"), arrived at here from the decode-share data rather than the call-count
data.

---

## 5. Degeneration-onset / cheap A/B-truncation analysis (just computed — numbers included)

A full 344-turn A/B is expensive. If the degeneracy signal already exceeds the **noise floor** at an
earlier turn, the A/B can be **truncated** there. We computed the crossing turns from the three
control runs, using **cumulative tokens** as the A/B unit and **noise = the max–min band of the 3
controls** (a deliberately *loose* noise estimate — see the caveat).

### 5.1 Noise floor

- Run-level cumulative **CV ≈ 0.9%** (spread ≈ **219k tok** across the 3 controls).
- A mid-run **noise HUMP** spans turns ~**150–225**, peaking at **5.8% @ turn 200**, driven by
  detail-call-count variance; it settles to ≈ **2.1% by turn 325**.
- The PC web itself is **non-deterministic**: **105 / 103 / 110** relationships across r1/r2/r3. (This
  is a real source of A/B noise and a caveat for §2's single-snapshot 105 figure.)

### 5.2 DELTA-OUTPUT lever truncation

- Robust **sustained 2x signal at turn ≈ 216** (≈ **4.8 h/run**, ≈ **29 GPU-h** for 3+3 runs =
  **−53%** vs ≈ 62 GPU-h full). **3x at turn ≈ 252.**
- A tempting **pre-hump window at turn 125** (S/N **2.98**, ≈ **13 GPU-h**, **−79%**) exists **but is
  risky** — it sits right before the noise hump (§5.1), so a real effect could be masked or a noise
  excursion mistaken for signal. Treat turn-125 truncation as aggressive; prefer ~216.

### 5.3 RELATIONSHIP-DEMOTION lever truncation

- **NOT meaningfully truncatable on the TOTAL-token metric** (≈ turn **287** for 2x, **never** 5x),
  because the stale-relationship tax is **diluted** by the other ~6 detail calls in the per-turn total.
- **BUT** the tax is **concentrated in the single PC call**. If the harness measured **PER-ENTITY**
  (PC-call-isolated) tokens, the S/N jumps several-fold and the signal would resolve by **≈ turn 150**.
- **Therefore the cheap lever for relationship-demotion is a METRIC change** (per-entity
  instrumentation), **not** truncation. This is why §7.1 bundles per-entity instrumentation with the
  raw-IO capture.

---

## 6. CAVEATS / WEAK POINTS (read this if you are the adversary)

This section is the point of the document. These are the load-bearing assumptions most likely to be
wrong, ordered by how much damage each does if it fails.

### 6.1 (MOST DAMAGING) The output-degeneracy thesis rests on a PROXY, not a captured completion

- The §2.2 "~80% restatement" and §3 LEVER-1 savings **ASSUME** the `entity_detail` **output**
  re-emits the embedded **105-relationship array** every call.
- **This is UNVERIFIED.** Raw `entity_detail` **completions are NOT logged anywhere**; the output
  analysis used the **parsed catalog as a proxy**.
- **If the model instead emits only the ~16 body fields** (and not the relationship array), then the
  absolute output saving from delta-output **shrinks ≈ 10x**, and **delta-output becomes
  non-truncatable too** (its §5.2 crossing turns move out past the run, like relationship-demotion's
  do in §5.3).
- **Consequence:** the entire §3–§5 case for LEVER-1 being the cheap "first" lever is contingent on a
  measurement we have not taken. **§7.1 is designed to take exactly that measurement before LEVER-1 is
  funded.** A reviewer who wants to kill this plan should attack here first.

### 6.2 Staleness is reconstructed from `history[]`, which may overstate it

- The "48 stale / age-340" figures (§2.1) come from each relationship's `history[]`, which records
  **changes**, not every **read/confirmation**. A bond confirmed but unchanged leaves no history entry.
- So the staleness counts may be **slightly overstated** — some "stale" bonds may have been silently
  re-confirmed. The *direction* (a large stale tail, never demoted) is robust; the exact 48/105 split
  is soft.

### 6.3 Onset crossings use only 3 controls → truncation turns are LOWER BOUNDS

- The §5 noise floor uses **max–min of 3 runs**, which **underestimates true variance** (3 samples is
  too few for a real spread, and max–min is not a confidence interval).
- Treat every truncation turn in §5 as a **lower bound**. Push them **10–20 turns later** for safety
  before committing GPU. The turn-125 aggressive window (§5.2) should likely be discarded.

### 6.4 The whole chain rests on ONE deeply-inspected entity

- The content inspection (§2) is **one entity** (`char-player`) in depth, plus aggregate stats. It is
  **not yet generalized** to all entities. The PC is the worst case (appears nearly every turn,
  uncapped permanent web), so it is the right entity to start with — but "the PC is degenerate" does
  not establish "the typical entity is degenerate", and LEVER-1's aggregate savings depend on
  behavior across the whole catalog, not just the PC.

### 6.5 Secondary honesty flags

- §2.3's "~600 tok illegal bloat re-emitted every call" is a **proxy** claim (the merge-persistence
  bug is verified; the per-call re-emission is inferred — see the §2.3 flag).
- The 105-relationship snapshot (§2.1) is from one control run; the web is non-deterministic
  (105/103/110, §5.1), so treat 105 as representative, not exact.
- The `entity_detail` slope (~64 tok/turn) and epic #477's total slope (~88.6/~151 tok/turn) are
  different scopes; do not cross-compare them as if they were the same series.

---

## 7. THE PLAN (sequenced, each step gated)

Each step is gated so the **cheapest measurement resolves the biggest caveat first**.

### 7.1 RAW-IO CAPTURE instrumentation + PER-ENTITY token instrumentation (measurement only)

- **Purpose:** resolve the §6.1 caveat (capture **real prompts AND completions**) and unlock the cheap
  relationship-demotion A/B (§5.3, per-entity metric).
- **Shape:** measurement-only, **default-OFF**, ~no cost — modeled on the existing S0-shadow /
  `return_uncompacted` instrumentation pattern (`format_detail_prompt(..., return_uncompacted=True)`
  at [tools/semantic_extraction.py L2090](../tools/semantic_extraction.py#L2090); the S0
  relevance-shadow measurement block at
  [L4073+](../tools/semantic_extraction.py#L4073)).
- **Validates:** the §2.2 ~80%-restatement thesis and the §6.1 "relationship-array-in-output" thesis
  on **real captured data**, not a proxy.
- **Adds:** per-entity (PC-call-isolated) token instrumentation so the relationship-demotion lever
  becomes A/B-cheap (§5.3).

### 7.2 DELTA-OUTPUT lever design (axis A / cost win + B-enabler)

- **Pre-checks before GPU:** (i) effect > noise floor (§5.1), **and (ii) §7.1 confirms the output
  actually contains the restatement** — if §6.1 breaks the wrong way, this lever is re-scoped or
  dropped here, before spend.
- **A/B:** cheap, truncated at **≈ turn 216** (§5.2), pushed later per §6.3.
- **Includes** the PC stable-attr allowlist fix at merge (§2.3 bug).

### 7.3 RELATIONSHIP-DEMOTION lever (#495, axis B / window)

- Uses the **per-entity metric** from §7.1.
- **A/B by ≈ turn 150** (§5.3), pushed later per §6.3.
- Parallel-safe with #494's code path (per epic #477 dependency notes).

### 7.4 (LATER) Checkpoint reset (#496) for full plateau

- Periodic reset/rebuild to a bounded floor (the only thing that flattens the carried-context slope —
  §3 "NOT levers"). Highest coreference risk → ships **last**, most conservative.

### Standing rule (carried from epic #477, restated as it produced this doc)

1. **Ground in current `origin/main` + measured logs, not stale design docs.** `git fetch` first.
   Treat #478/#479 and the A1a #492 framing as **superseded** where they conflict with current truth
   (the specific stale premise: the pre-L1 "uncapped PC web").
2. **TWO pre-checks before ANY GPU spend:** (i) effect > #488 noise floor; (ii) for bounding levers,
   late-window **slope → 0** (asymptotic-bound test), not merely beating the baseline by X%.
3. **Non-blind by construction:** every lever must move a per-turn field the #488 paired scorer reads
   (the A0 #484 lesson).
4. **SEMANTIC validity asserted BEFORE optimizing magnitude.** This is the lesson that produced this
   whole doc: §2 (is the content actually degenerate?) gates §3 (which lever?), which gates §5 (how
   cheaply can we test it?). Do not optimize a number until the content behind it is shown to be
   cruft.

---

## Appendix A — Verified code citations (origin/main @ cf22a27)

| Claim | Location |
|---|---|
| Detail-call cap = 6 | [tools/semantic_extraction.py L1163](../tools/semantic_extraction.py#L1163) |
| Detail-cap selection (PC always kept; new/existing by confidence) | [tools/semantic_extraction.py L4049-L4071](../tools/semantic_extraction.py#L4049-L4071) |
| Non-PC scene relationship cap = 8 | [tools/semantic_extraction.py L1160](../tools/semantic_extraction.py#L1160) |
| PC volatile-tail cap = 10 (L1 tiering) | [tools/semantic_extraction.py L1229](../tools/semantic_extraction.py#L1229) |
| PC rel type-tiering (permanent uncapped) | [tools/semantic_extraction.py L1776](../tools/semantic_extraction.py#L1776) |
| PC stable-attr allowlist (injection trim) | [tools/semantic_extraction.py L1979](../tools/semantic_extraction.py#L1979) |
| `_PC_KEY_STABLE_ATTRS` = species/race/class/aliases | [tools/semantic_extraction.py L1142](../tools/semantic_extraction.py#L1142) |
| Prior-context formatter (prompt reconstruction source) | [tools/semantic_extraction.py L1922](../tools/semantic_extraction.py#L1922) |
| `format_detail_prompt` (+ `return_uncompacted` instrumentation hook) | [tools/semantic_extraction.py L2090](../tools/semantic_extraction.py#L2090) |
| Merge does NOT enforce PC allowlist (the §2.3 bug) | [tools/catalog_merger.py L1196-L1206](../tools/catalog_merger.py#L1196-L1206) |
| Relevance signal (reused by lever b selection) | [tools/catalog_merger.py L597](../tools/catalog_merger.py#L597) |
| PC stable-attr allowlist (template) | [templates/extraction/entity-detail.md L57](../templates/extraction/entity-detail.md#L57) |

## Appendix B — Data sources

- `framework/extraction-log.jsonl` — per-turn / per-phase token breakdown.
- `/data/nse-agent/l2-ab/l2-control-r1`, `…-r2`, `…-r3` (arclight) — the 3 control runs used for the
  noise floor and onset analysis (§5).
- `llama-vk*.log` (arclight) — server-side decode/prefill timing split (§1.3).
- Round-53 char-player semantic inspection (turn-10 vs turn-344), prompt **reconstructed** via
  `format_detail_prompt` / `_format_prior_entity_context` (§2).
