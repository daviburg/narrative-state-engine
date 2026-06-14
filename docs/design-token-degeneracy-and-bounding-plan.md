# Token Degeneracy & Bounding Plan — Consolidated Analysis (for Adversarial Review)

**Status:** Analysis + plan, **CORRECTED after adversarial review**. Authored for **adversarial
review**. Its explicit purpose was to be attacked: if a claim below is wrong, the plan that depends on
it should be killed before any GPU spend. The review did exactly that — see the correction note
immediately below. Read **§6 (Caveats / Weak Points)** for the load-bearing assumptions, several of
which the review **refuted**.

> ## ⚠ Correction history — what the adversarial review refuted (2026-06-13)
>
> A GPT-5.5 adversarial review
> ([PR #500 comment](https://github.com/daviburg/narrative-state-engine/pull/500#issuecomment-4700301603))
> **refuted the central output-degeneracy claim** of the first draft. This revision keeps the error on
> record rather than silently rewriting it:
>
> 1. **The draft claimed the `entity_detail` OUTPUT re-emits the full ~105-relationship array every
>    call (→ ~80% restatement). This is REFUTED.** The `entity-detail.md` schema requests only the
>    ~11 body fields and **no `relationships` field**; relationships are produced by a **separate
>    `relationship_mapper` phase**. The draft used the *parsed final catalog* as a proxy for the
>    completion and so conflated "the catalog has relationships" with "`entity_detail` emitted them".
>    The ~80%-restatement / ~1500k-cumulative / delta-output savings case is **unsupported** until raw
>    captured completions prove the model violates its schema. (§2.2, §6.1.)
> 2. **All delta-output truncation/savings numbers (the turn-216 onset etc.) are WITHDRAWN** — they
>    rested on the refuted relationship-array-in-output assumption. (§5.2.)
> 3. **The "merge has no PC allowlist guard" bug description was REFUTED** — the PC `entity_detail`
>    path **is** guarded at `cf22a27`. The real, smaller inconsistency is an allowlist mismatch
>    (`_PC_KEY_STABLE_ATTRS` vs `PC_ALLOWED_ATTRS`); the "14 illegal keys on disk" example came from
>    **older control runs (commit `609473`, June 10)**, not current main. (§2.3.)
> 4. **Appendix A line anchors were stale** and have been replaced with current-`cf22a27` lines.
> 5. **The n=3 max–min onset crossings are exploratory lower bounds, not decision-grade thresholds.**
> 6. **The analytic `>100 turns` staleness cutoff (and any lever threshold) is flagged as a
>    config/swept parameter, not a magic constant** (Rule 10), alongside the existing magic windows.
>
> **Net effect on the plan:** delta-output is **demoted and gated** behind raw-output capture (it may
> be rescoped or dropped); **relationship-demotion is promoted** to the better-supported lever
> (stale-active relationships are real in the controls and the input/window case does not depend on the
> refuted output claim). See the corrected §7.

**Epic:** [#477](https://github.com/daviburg/narrative-state-engine/issues/477) — Stabilize per-turn
extraction token demand at quality parity.
**Sub-issues this plan sequences:** [#494](https://github.com/daviburg/narrative-state-engine/issues/494)
(lever b, relevance-scoped detail), [#495](https://github.com/daviburg/narrative-state-engine/issues/495)
(lever c-i, cap+age PC web), [#496](https://github.com/daviburg/narrative-state-engine/issues/496)
(lever c-ii, checkpoint reset).
**Companion design (current truth):** `docs/design-bounded-context-relevance-reset.md`.

**Grounding:** `origin/main` @ `cf22a27` (`feat: relevance-scoped entity_detail selection (S1a
recover-only, A2a lever b) (#499)`), fetched 2026-06-13; this doc lives on branch
`docs/token-degeneracy-analysis` whose code tree is `cf22a27`. All code citations are line numbers in
that tree (re-verified after the review — the first draft's anchors were stale). All token/log numbers
are from the three L2 control runs (`l2-control-r1/r2/r3`) under `/data/nse-agent/l2-ab/` on the
`arclight` host, the per-turn `framework/extraction-log.jsonl`, and the `llama-vk*.log` server-timing
logs, plus the round-53 char-player semantic inspection. **Caveat the review surfaced:** those control
runs were generated at commit `609473` (June 10), which is **older than `cf22a27`** (June 13) — so any
*on-disk catalog* artifact may reflect older code, not current main. Where a number could not be
measured directly (raw prompts/completions are **not** logged anywhere — see §6), this doc says so
explicitly and labels the value a **proxy**.

> **Honesty contract.** Conclusions in §1–§5 are stated at the confidence the evidence supports, and
> the §0 correction note records where the first draft was **wrong**. §6 enumerates the load-bearing
> assumptions; the most damaging one (§6.1, the output thesis) was **refuted** by the review and is now
> marked as such. The plan in §7 is sequenced so the cheapest step (raw-IO + per-entity capture, §7.1)
> resolves the remaining measurement gap **before** any lever is funded — and delta-output is gated
> behind that capture rather than assumed.

---

## 0. TL;DR for the reviewer

1. **Two different axes are being conflated in casual discussion and we separate them here.** Token
   *count* growth is an **INPUT** phenomenon (context-window risk). GPU *cost* is an **OUTPUT/decode**
   phenomenon (87% of wall-clock). They grow for different reasons and need different levers (§1). The
   axis separation is directionally sound for GPU cost; the *output-flatness* sub-claim is
   **not verifiable from the current logs** (no output-token field exists — §1.2, §6.1).
2. **There is real, content-verified degeneracy on the INPUT side** of the char-player (PC)
   `entity_detail` path — not merely rising counts. ~half the injected PC relationship web is stale
   cruft that is never demoted (§2.1, **confirmed** in control r1). **The OUTPUT-restatement claim of
   the first draft was REFUTED** (the proxy conflated two extraction phases — §2.2, §6.1); it is now
   downgraded to an unsupported estimate pending raw-output capture.
3. **The better-supported lever is RELATIONSHIP-DEMOTION** (age out stale PC bonds; attacks the
   injected web = context window). **DELTA-OUTPUT** (stop re-emitting unchanged fields; attacks decode
   = cost) **is now gated** behind raw-output capture, because its premise — that `entity_detail`
   re-emits the relationship array — is contradicted by the requested schema (§2.2, §3, §6.1).
4. **Cost (A) was argued to be a prerequisite to affordably test window ideas (B)** (each B experiment
   costs ~3 GPU-days). That ordering held only while delta-output looked like the cheap first lever;
   with delta-output now gated, the (A)-before-(B) sequencing is **contingent** on the raw-capture
   result (§4).
5. **A/B runs can be truncated early** if the degeneracy signal already clears the noise floor — but
   the previously-quoted delta-output crossing (turn ≈ 216) is **WITHDRAWN**: it depended on the
   refuted relationship-array-in-output assumption. Relationship-demotion is **not** truncatable on the
   total-token metric and instead needs a **per-entity instrumentation** change to become cheap (§5).
   All onset turns are **exploratory lower bounds** (n=3), not decision-grade thresholds (§5, §6.3).
6. **The whole chain rests on one deeply-inspected entity + aggregate stats; the output thesis rested
   on a proxy that was refuted** (§6). The plan's first funded step is measurement, not optimization.

---

## 1. The reconciliation: input grows (cheap), output is flat (expensive)

The starting tension: people see "tokens grow unbounded" and "GPU time grows 4x" and assume they are
the same problem. They are not. The reconciliation:

### 1.1 INPUT / prompt tokens grow, ~linearly, with no plateau

- `entity_detail` `raw_input` ≈ **5,252 tok @ turn 1 → ≈ 42,312 tok @ turn 344**, slope ≈ **64
  tok/turn** (per `extraction-log.jsonl`, confirmed across `l2-control-r1/r2/r3`; the review
  reproduced per-run slopes of ≈ **69.7 / 69.2 / 64.8 tok/turn**, consistent).
- Per-turn **total** input ≈ **15.9k → 51.9k tok** over the same span. **Review correction:** the
  review's read of the same three control JSONLs gave **total_raw ≈ 12.4k–15.0k @ turn 1 →
  ≈ 58.1k–64.4k @ turn 344**; the 15.9k→51.9k headline does **not** match and should be treated as a
  rough early figure, not a measured one. The *shape* (linear input growth, no plateau) is robust.
- This is the context-window risk: at 300+ turns the prompt approaches the model's logical window.
- **Note:** epic #477's earlier headline slope was `15182 + 88.6·turn_index` (total) and "~151 tok/turn
  late-window". Those and the ~64 tok/turn `entity_detail`-only slope here are consistent — different
  scopes (total vs. one phase) and different fit windows. We use the per-phase `entity_detail` slope
  because that phase is the dominant and the one the levers touch.

### 1.2 OUTPUT / decode is FLAT (claim NOT verifiable from current logs)

- `entity_detail` output ≈ **388 → 447 tok/call** across the whole run — it **plateaus**, it does not
  grow with session depth.
- **Review correction:** these output-token figures are **not present in `extraction-log.jsonl`** —
  the only per-phase metric keys are `avg_per_call, calls, … raw_input_tokens`; there is **no output /
  completion token field at all** (`top_level_keys_containing_output []`,
  `prompt_metric_keys_containing_output []`). So the 388→447 figure could not be reproduced from the
  raw logs and must be treated as **unverified** until §7.1 output-token capture lands.
- Per-turn output is **bounded by the calls/turn cap**: `_MAX_DETAIL_ENTITIES_PER_TURN = 6`
  ([tools/semantic_extraction.py L1163](../tools/semantic_extraction.py#L1163)), enforced in the
  detail-cap selection block around
  [tools/semantic_extraction.py L4234-L4246](../tools/semantic_extraction.py#L4234-L4246).

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
[`_format_prior_entity_context` (L2107)](../tools/semantic_extraction.py#L2107) against the catalog
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

- Of **105 relationships**, **48 are STALE** (>100 turns since last update — see the Rule-10 flag
  below), and **ALL are still marked "active"** — never demoted. Verbatim examples from the inspection
  (all **confirmed** by the review against control r1's `char-player.json`):
  - `located_at loc-icy-path` — last updated **turn-4**, age **340** turns, still active @344.
  - `captive_of char-unknown-figures` — narratively resolved ~**turn-29**, still active @344.
  - `received_broth_from …` — a one-off interaction, age **313**, still active.
- ≈ **half the web (~7,000 tok) is CRUFT**; the other half (current bonds — gorok / renn / finn) is
  **VALID**.

> **Rule-10 flag (#447):** the `>100 turns` staleness cutoff used here is an **analytic threshold for
> the inspection**, not a sanctioned constant. Any demotion **lever** must derive its cutoff from a
> **config/swept parameter** or from the `relationship_mapper` semantic `status` field (which already
> distinguishes `active` / `resolved`), **never** a hardcoded magic number. The related existing magic
> windows the reviewer flagged as tech debt — `_SCENE_REL_RECENCY_WINDOW = 10`
> ([L1159](../tools/semantic_extraction.py#L1159)), `_REL_RECENCY_WINDOW = 15`
> ([L1155](../tools/semantic_extraction.py#L1155)),
> `_PC_REL_VOLATILE_TAIL_CAP_DEFAULT = 10` ([L1229](../tools/semantic_extraction.py#L1229)) — are in
> the same bucket and should be consolidated/swept rather than extended.

> **Cross-run caveat (review):** the stale split is run-sensitive — r1 = 105 rels / 48 stale, r2 = 103
> / 54, r3 = 110 / 65. The *direction* (large never-demoted stale tail) holds in all three; the exact
> 48/105 figure is one run's snapshot, not a constant.

> Conclusion: the injected input is **NOT uniformly valid**. About half of the biggest input
> component is stale relationships that were never demoted from "active". This is the degeneracy on
> the input axis, and it is **specific and addressable** (demote stale bonds), not a blanket "shrink
> the prompt".

This is consistent with the code: L1 type-tiering
([`_apply_pc_rel_type_tier`, L1961](../tools/semantic_extraction.py#L1961)) caps the *volatile tail*
at `pc_rel_volatile_tail_cap = 10`
([`_PC_REL_VOLATILE_TAIL_CAP_DEFAULT = 10`, L1229](../tools/semantic_extraction.py#L1229)) but leaves
**permanent-bond types UNCAPPED by design**, and **nothing ages a permanent bond out of "active"**.
That is exactly the gap #495 (lever c-i) targets.

### 2.2 OUTPUT side — the ~80% restatement claim was REFUTED (proxy conflated two phases)

> **This subsection is the one the adversarial review overturned.** The first draft claimed the
> `entity_detail` **output** re-emits the whole entity — *including the ~105-relationship array* — so
> ≈80% of every completion is restatement. **That is unsupported and most likely wrong**, for two
> independent reasons:
>
> 1. **By schema, `entity_detail` output contains NO relationships.** The `entity-detail.md` template
>    asks for a single JSON object under key `"entity"` with the ~11 body fields (`id, name, type,
>    identity, current_status, status_updated_turn, stable_attributes, volatile_state, first_seen_turn,
>    last_updated_turn, notes`) and **no `relationships` field**
>    ([templates/extraction/entity-detail.md L7-L58](../templates/extraction/entity-detail.md#L7)).
> 2. **Relationships come from a SEPARATE phase.** The `relationship_mapper` phase
>    ([templates/extraction/relationship-mapper.md](../templates/extraction/relationship-mapper.md),
>    invoked via `load_template("relationship-mapper")` at
>    [tools/semantic_extraction.py L4444](../tools/semantic_extraction.py#L4444) /
>    [L4510-L4516 (parallel submit)](../tools/semantic_extraction.py#L4510), merged via
>    `merge_relationships(...)` at
>    [L4678](../tools/semantic_extraction.py#L4678)) is what produces the `relationships` array. The
>    parsed catalog the draft used as a proxy therefore contains relationships **from that other
>    phase**, not from `entity_detail` output. The draft mistook *"the catalog has relationships"* for
>    *"`entity_detail` emitted them"* — a phase conflation.
>
> **Consequence:** the ~80%-restatement figure, the implied ~1,500k cumulative output, and the entire
> delta-output saving/truncation case (§3 delta-output / LEVER-B, §5.2) are **UNSUPPORTED** as written and are
> withdrawn pending raw captured completions (§7.1). They would only be revived if captured output
> shows the model **violating its schema** by emitting relationships anyway.

**What can still be said, at low confidence (NOT decision-grade):** the body fields the model *is*
asked to re-emit — `current_status`, `volatile_state`, and the (allowlisted) `stable_attributes` —
appear largely static across the late run in the parsed-catalog provenance:

- `current_status` + `volatile_state` had not materially changed since ~turn-195 (≈149 turns) in the
  inspected entity, yet the schema requires the model to re-state `current_status` "always" and
  `volatile_state` as a full snapshot every call.
- ~11 of 14 `stable_attributes` keys were unchanged for 90–220 turns.

This suggests a **body-field-only** restatement that delta-output *could* attack — but it is a
**small, unvalidated estimate** computed from the catalog proxy (not a captured completion), it
**excludes** the (refuted) relationship savings, and it must be confirmed against raw output before it
is treated as a real cost case. See §6.1.

### 2.3 OUTPUT side — the PC stable-attribute allowlist INCONSISTENCY (bug description CORRECTED)

> **The first draft's bug description ("the MERGE path has no PC allowlist guard") was REFUTED.** At
> `cf22a27` the PC `entity_detail` path **is** guarded, before and after merge.

- The PC stable-attribute allowlist is `species / race / class / aliases` in the template
  ([templates/extraction/entity-detail.md L57](../templates/extraction/entity-detail.md#L57)), which
  matches `_PC_KEY_STABLE_ATTRS = {"species","race","class","aliases"}`
  ([tools/semantic_extraction.py L1142](../tools/semantic_extraction.py#L1142)).
- **The PC path IS guarded at merge.** After the PC detail completion, the code calls
  `_filter_pc_attributes(entity_data)` ([L163](../tools/semantic_extraction.py#L163), applied at
  [L4570](../tools/semantic_extraction.py#L4570)), then `merge_entity(...)`, then
  `_sanitize_pc_catalog_entry(catalogs)` ([L179](../tools/semantic_extraction.py#L179), applied at
  [L4573](../tools/semantic_extraction.py#L4573)) which purges disallowed keys from the on-disk entry.
  The partial-merge fallback is guarded too — it skips any key not in `PC_ALLOWED_ATTRS`
  ([L2853](../tools/semantic_extraction.py#L2853),
  [L2861](../tools/semantic_extraction.py#L2861)). The generic `catalog_merger` deep-merge at
  [tools/catalog_merger.py L1196-L1206](../tools/catalog_merger.py#L1196) does have no PC guard, but
  it is **wrapped** by these PC-specific filters on the PC path, so the first draft's "illegal keys
  reach disk and are never cleaned" mechanism does **not** hold at `cf22a27`.
- **The on-disk "14 illegal keys" example came from OLDER code.** The control catalogs were generated
  at commit `609473` (June 10), not `cf22a27` (June 13) — so those artifacts predate (or differ from)
  the current guards and cannot be cited as a current-main bug.
- **The real, smaller inconsistency that survives** is an **allowlist mismatch between two code
  paths**: the injection trim uses `_PC_KEY_STABLE_ATTRS = {species, race, class, aliases}`
  ([L2164](../tools/semantic_extraction.py#L2164)), while `_filter_pc_attributes` uses the broader
  `PC_ALLOWED_ATTRS` ([L79](../tools/semantic_extraction.py#L79) —
  `race, class, abilities, appearance, hp_change, condition, equipment, quest, allegiance, status,
  aliases`), which **omits `species`** and admits keys the injection trim would drop. That asymmetry
  can let a key persist on disk that injection would never show — a **correctness** wart worth
  reconciling — but it is far narrower than the "no guard at all" claim, and its **token** impact is
  unquantified (and, per §2.2, not part of any validated output-cost case).

---

## 3. The two levers (re-ranked after the review)

The point of §2 is that the levers should be chosen **because the content shows a specific defect**.
After the review, the ranking flips: the input-side defect is **confirmed**, the output-side defect is
**unsupported**.

### LEVER A — RELATIONSHIP-DEMOTION (now the BETTER-SUPPORTED lever; attacks the injected web = CONTEXT WINDOW, axis B; this is #495)

Add **recency/status-aware demotion** of PC relationships: a PC relationship cap plus **dormancy
demotion** of bonds that are stale-but-still-"active" (the §2.1 defect, **confirmed** in the control
catalogs). Estimated effect on the late PC call: ≈ **18.7k → 5–6k tok** (input-side; the late-call
decomposition itself is a reconstruction, so treat the magnitude as indicative). Crucially, this
lever **does not depend on the refuted output claim** — it attacks injected input, where the stale
tail is independently verified. The demotion cutoff must be a **config/swept parameter or derived from
`relationship_mapper` semantic status**, not a hardcoded `>100 turns` constant (Rule 10, §2.1 flag).

### LEVER B — DELTA-OUTPUT (DEMOTED + GATED; attacks the 87% DECODE = COST, axis A)

Emit **only CHANGED fields** instead of restating the whole entity. **The cost case is currently
unsupported** (§2.2, §6.1): the original rationale assumed the output re-emits the ~105-relationship
array, but by schema `entity_detail` output carries **no relationships** — those are a separate phase.
What *might* remain is body-field-only restatement (`current_status` / `volatile_state` /
allowlisted `stable_attributes`), but that is a small, unvalidated estimate. **This lever is gated
behind raw-output capture (§7.1)** and may be **rescoped or dropped** if captured completions show the
body-field restatement does not beat run-to-run variance without the (refuted) relationship savings.

### NOT levers (do not touch)

- The **fixed template** (2,175 tok) and the **current factual fields** (identity / status / volatile
  / stable *current values*) are **valid and ~flat** (§2.1 table). Compacting them is a one-time
  constant downshift that does not change the slope (this is the A1a #492 "dead premise" result in
  epic #477). To bound the window, **RESET** them periodically (lever c-ii / #496), do not compress.

---

## 4. The (A)–(B) unification (strategic insight — now CONTINGENT)

- Each (B) context-window experiment costs ≈ **3 GPU-days** (≥3 paired runs/variant per #488, at
  ~62 GPU-h for a full 344-turn A/B — see §5).
- (A) DELTA-OUTPUT cuts decode, which is the dominant wall-clock term (§1.3), so **if it has a real
  target** it would make every future (B) experiment cheaper.
- **Review correction:** that "(A) enables (B), do delta-output first" ordering held only while
  delta-output looked like the cheap, well-supported first lever. With the output claim refuted (§2.2),
  the (A)-first ordering is **contingent** on §7.1 raw-output capture showing a real body-field cost
  case. If capture shows the output already matches the schema (no large restatement), **(B)
  relationship-demotion leads** and delta-output is rescoped or dropped.

This is the same *dual-lever* intuition epic #477 reached for lever (b), but it can no longer be used
to justify funding delta-output ahead of measurement.

---

## 5. Degeneration-onset / cheap A/B-truncation analysis (just computed — numbers included)

A full 344-turn A/B is expensive. If the degeneracy signal already exceeds the **noise floor** at an
earlier turn, the A/B can be **truncated** there. We computed the crossing turns from the three
control runs, using **cumulative tokens** as the A/B unit and **noise = the max–min band of the 3
controls**. **Read the whole of §5 with two review corrections in mind:** (a) max–min of n=3 is **not
a confidence interval** and underestimates variance, so every crossing turn below is an **exploratory
LOWER BOUND, not a decision-grade threshold**; (b) the cumulative-token metric is an **input-side**
measure — it cannot validate an **output-delta** onset, and the logs contain **no output-token field**
at all (§1.2).

### 5.1 Noise floor

- Run-level cumulative **CV ≈ 0.9%** (spread ≈ **219k tok** across the 3 controls).
- A mid-run **noise HUMP** spans turns ~**150–225**, peaking at **5.8% @ turn 200**, driven by
  detail-call-count variance; it settles to ≈ **2.1% by turn 325**.
- The PC web itself is **non-deterministic**: **105 / 103 / 110** relationships across r1/r2/r3. (This
  is a real source of A/B noise and a caveat for §2's single-snapshot 105 figure.)

### 5.2 DELTA-OUTPUT lever truncation — WITHDRAWN

> **These numbers are withdrawn.** The turn-216 (2x) / turn-252 (3x) / turn-125 (aggressive) crossings
> were computed as an **output-delta** onset, but they rested on the **refuted** assumption that
> `entity_detail` output re-emits the relationship array (§2.2, §6.1). With that assumption gone, and
> with **no output-token field in the logs to fit against**, there is **no validated delta-output
> onset turn**. Any future delta-output truncation analysis must be recomputed from **raw captured
> completions** (§7.1), on a **body-field-only** restatement signal, and treated as a lower bound.
>
> *(Withdrawn original figures, retained for the record: "2x at turn ≈ 216 (−53% GPU), 3x at ≈ 252,
> aggressive pre-hump at turn 125." Do not use.)*

### 5.3 RELATIONSHIP-DEMOTION lever truncation

- **NOT meaningfully truncatable on the TOTAL-token metric** (≈ turn **287** for 2x — an exploratory
  lower bound, not independently re-verified in the raw logs the reviewer inspected; **never** 5x),
  because the stale-relationship tax is **diluted** by the other ~6 detail calls in the per-turn total.
- **BUT** the tax is **concentrated in the single PC call**. If the harness measured **PER-ENTITY**
  (PC-call-isolated) tokens, the S/N would rise and the signal would resolve earlier — the previously
  quoted "≈ turn 150" is a **hand-wavy estimate** until per-entity PC-call metrics actually exist
  (§7.1), so treat it as a hypothesis, not a number.
- **Therefore the cheap lever for relationship-demotion is a METRIC change** (per-entity
  instrumentation), **not** truncation. This is why §7.1 bundles per-entity instrumentation with the
  raw-IO capture. Because the underlying stale-tail defect is **confirmed** (§2.1), this is the
  lever with the firmest evidence base.

---

## 6. CAVEATS / WEAK POINTS (read this if you are the adversary)

This section is the point of the document. These are the load-bearing assumptions most likely to be
wrong, ordered by how much damage each does if it fails.

### 6.1 (REFUTED BY THE REVIEW) The output-degeneracy thesis was wrong, not merely a proxy

- The §2.2 "~80% restatement" and the original §3 delta-output (LEVER-B) savings **ASSUMED** the `entity_detail`
  **output** re-emits the embedded **105-relationship array** every call.
- **The review REFUTED this, not merely flagged it as unverified.** By the requested schema,
  `entity_detail` output contains **no `relationships` field** at all
  ([entity-detail.md L7-L58](../templates/extraction/entity-detail.md#L7)); relationships are produced
  by the **separate `relationship_mapper` phase**
  ([relationship-mapper.md](../templates/extraction/relationship-mapper.md); code at
  [tools/semantic_extraction.py L4444](../tools/semantic_extraction.py#L4444),
  [L4678](../tools/semantic_extraction.py#L4678)). The draft used the **parsed final catalog as a
  proxy** and so attributed relationships from one phase to the output of another.
- **Consequence:** the absolute output saving from delta-output **shrinks ≈ 10x** (and possibly to
  near-zero on relationships), and **delta-output becomes non-truncatable** (its §5.2 crossing turns
  are withdrawn). The output entity-detail parser *tolerates* stray relationship keys, but tolerance
  is **not** the requested output contract — so the only way the original thesis revives is if **raw
  captured completions prove the model is disobeying its schema** and emitting relationships anyway.
- **What remains as a (small, unvalidated) cost target** is body-field-only restatement
  (`current_status` / `volatile_state` / allowlisted `stable_attributes`), which §7.1 will measure on
  real captured data before LEVER-B (delta-output) is funded. **A reviewer was right to attack here
  first; this is where the plan changed.**

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
  not establish "the typical entity is degenerate", and any aggregate lever savings depend on
  behavior across the whole catalog, not just the PC.

### 6.5 Secondary honesty flags

- §2.3's allowlist issue is a **correctness inconsistency** (`_PC_KEY_STABLE_ATTRS` vs
  `PC_ALLOWED_ATTRS`), **not** the "no guard at merge" bug the first draft asserted; its **token**
  impact is unquantified and is **not** part of any validated output-cost case. The on-disk "14 keys"
  example came from **older control code** (`609473`), not current main.
- The 105-relationship snapshot (§2.1) is from one control run; the web is non-deterministic
  (105/103/110, §5.1), so treat 105 as representative, not exact.
- The `entity_detail` slope (~64 tok/turn) and epic #477's total slope (~88.6/~151 tok/turn) are
  different scopes; do not cross-compare them as if they were the same series.
- The §1.2 output figures (388→447 tok/call) and the §1.1 total-input headline (15.9k→51.9k) could not
  be reproduced from the control logs; treat both as **unverified** pending §7.1 (output) and a re-fit
  (total input).

---

## 7. THE PLAN (sequenced, each step gated) — CORRECTED after review

Each step is gated so the **cheapest measurement resolves the biggest caveat first**. The review's
sequence verdict: `raw-IO capture → delta-output → relationship-demotion → reset` does **not** hold as
written; the defensible order is **raw-IO + per-entity capture → decide whether delta-output still has
a measured cost case → relationship-demotion for window pressure → reset later**.

### 7.1 RAW-IO CAPTURE instrumentation + PER-ENTITY token instrumentation (measurement only — SOUND, endorsed)

- **Status:** in progress; **endorsed** by the review as the right first step.
- **Purpose:** resolve the §6.1 gap (capture **real prompts AND completions**, including an
  **output/completion token field** the logs currently lack) and unlock the cheap
  relationship-demotion A/B (§5.3, per-entity metric).
- **Shape:** measurement-only, **default-OFF**, ~no cost — modeled on the existing
  `return_uncompacted` instrumentation pattern (`format_detail_prompt(..., return_uncompacted=True)`
  at [tools/semantic_extraction.py L2275](../tools/semantic_extraction.py#L2275), hook at
  [L2282](../tools/semantic_extraction.py#L2282); existing measurement call sites at
  [L1712](../tools/semantic_extraction.py#L1712) and
  [L4413-L4430](../tools/semantic_extraction.py#L4413)).
- **Validates / refutes:** the §2.2 body-field-restatement estimate and the §6.1
  "relationship-array-in-output" question on **real captured data**, not a proxy. (The schema already
  says relationships are NOT requested — this step confirms whether the model obeys it.)
- **Adds:** per-entity (PC-call-isolated) token instrumentation so the relationship-demotion lever
  becomes A/B-cheap (§5.3).

### 7.2 DELTA-OUTPUT DECISION GATE (axis A / cost — may be RESCOPED or DROPPED)

- **This is now a decision gate, not a funded lever.** Proceed **only if** §7.1 captured completions
  show **body-field restatement** (`current_status` / `volatile_state` / allowlisted
  `stable_attributes`) large enough to **beat run-to-run variance WITHOUT any relationship-array
  savings** (the relationship savings are refuted — §2.2, §6.1).
- **If capture shows the output already matches the schema** (no large restatement, no schema-violating
  relationship emission), **delta-output is rescoped or DROPPED here, before any GPU spend.**
- If it survives the gate: recompute a fresh, body-field-only truncation onset from the captured
  output (the §5.2 turn-216 figure is **withdrawn**), and include the §2.3 allowlist reconciliation as
  a correctness fix (not a token-savings claim).

### 7.3 RELATIONSHIP-DEMOTION lever (#495, axis B / window — now the BETTER-SUPPORTED lever, PROMOTED)

- **Promoted** ahead of delta-output: the stale-but-active tail is **confirmed** in the controls
  (§2.1), the input/window win does **not** depend on the refuted output claim, and the per-entity
  metric from §7.1 makes its A/B cheap.
- Uses the **per-entity metric** from §7.1; onset turn to be measured (the old "≈ turn 150" is a
  hypothesis, §5.3), treated as a lower bound per §6.3.
- **Demotion cutoff must be a config/swept parameter or derived from `relationship_mapper` semantic
  `status`**, never a hardcoded `>100 turns` constant (Rule 10, §2.1 flag). Fold in the existing magic
  windows (`_SCENE_REL_RECENCY_WINDOW`, `_REL_RECENCY_WINDOW`, `_PC_REL_VOLATILE_TAIL_CAP_DEFAULT`) as
  related tech debt to consolidate.
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

## Appendix A — Verified code citations (origin/main @ cf22a27, RE-VERIFIED after review)

> The first draft's anchors were stale (the review caught several pointing 100–300 lines off). Every
> line below was re-checked against the `cf22a27` tree on 2026-06-13.

| Claim | Location |
|---|---|
| Detail-call cap = 6 (`_MAX_DETAIL_ENTITIES_PER_TURN`) | [tools/semantic_extraction.py L1163](../tools/semantic_extraction.py#L1163) |
| Detail-cap selection (PC kept; cap applied) | [tools/semantic_extraction.py L4234-L4246](../tools/semantic_extraction.py#L4234-L4246) |
| Non-PC scene relationship recency window = 10 (`_SCENE_REL_RECENCY_WINDOW`) | [tools/semantic_extraction.py L1159](../tools/semantic_extraction.py#L1159) |
| Relationship recency window = 15 (`_REL_RECENCY_WINDOW`) | [tools/semantic_extraction.py L1155](../tools/semantic_extraction.py#L1155) |
| PC volatile-tail cap = 10 (`_PC_REL_VOLATILE_TAIL_CAP_DEFAULT`, L1 tiering) | [tools/semantic_extraction.py L1229](../tools/semantic_extraction.py#L1229) |
| PC rel type-tiering (permanent uncapped) `_apply_pc_rel_type_tier` | [tools/semantic_extraction.py L1961](../tools/semantic_extraction.py#L1961) |
| PC stable-attr allowlist (injection trim to `_PC_KEY_STABLE_ATTRS`) | [tools/semantic_extraction.py L2164](../tools/semantic_extraction.py#L2164) |
| `_PC_KEY_STABLE_ATTRS` = species/race/class/aliases | [tools/semantic_extraction.py L1142](../tools/semantic_extraction.py#L1142) |
| `PC_ALLOWED_ATTRS` (broader allowlist; omits `species`) | [tools/semantic_extraction.py L79](../tools/semantic_extraction.py#L79) |
| `_filter_pc_attributes` (PC merge guard, before merge) | [tools/semantic_extraction.py L163](../tools/semantic_extraction.py#L163) |
| `_sanitize_pc_catalog_entry` (PC merge guard, after merge) | [tools/semantic_extraction.py L179](../tools/semantic_extraction.py#L179) |
| PC detail path applies both guards around merge | [tools/semantic_extraction.py L4570-L4573](../tools/semantic_extraction.py#L4570-L4573) |
| Partial-merge PC allowlist guard | [tools/semantic_extraction.py L2853-L2861](../tools/semantic_extraction.py#L2853-L2861) |
| Prior-context formatter (prompt reconstruction source) `_format_prior_entity_context` | [tools/semantic_extraction.py L2107](../tools/semantic_extraction.py#L2107) |
| `format_detail_prompt` (+ `return_uncompacted` instrumentation hook) | [tools/semantic_extraction.py L2275](../tools/semantic_extraction.py#L2275) |
| Relationship-mapper = SEPARATE phase (template load + merge) | [tools/semantic_extraction.py L4444](../tools/semantic_extraction.py#L4444), [L4678](../tools/semantic_extraction.py#L4678) |
| Relationship-mapper standalone runner | [tools/semantic_extraction.py L3725-L3745](../tools/semantic_extraction.py#L3725-L3745) |
| Generic deep-merge of stable_attributes (no PC guard; wrapped by PC filters) | [tools/catalog_merger.py L1196-L1206](../tools/catalog_merger.py#L1196-L1206) |
| Relevance ordering (reused by lever b selection) `select_relevant_entities` | [tools/catalog_merger.py L603](../tools/catalog_merger.py#L603) |
| PC stable-attr allowlist (template) | [templates/extraction/entity-detail.md L57](../templates/extraction/entity-detail.md#L57) |
| `entity_detail` output schema (NO relationships field) | [templates/extraction/entity-detail.md L7-L58](../templates/extraction/entity-detail.md#L7) |
| `relationship_mapper` output schema (relationships array, separate phase) | [templates/extraction/relationship-mapper.md L54](../templates/extraction/relationship-mapper.md#L54) |

## Appendix B — Data sources

- `framework/extraction-log.jsonl` — per-turn / per-phase token breakdown.
- `/data/nse-agent/l2-ab/l2-control-r1`, `…-r2`, `…-r3` (arclight) — the 3 control runs used for the
  noise floor and onset analysis (§5).
- `llama-vk*.log` (arclight) — server-side decode/prefill timing split (§1.3).
- Round-53 char-player semantic inspection (turn-10 vs turn-344), prompt **reconstructed** via
  `format_detail_prompt` / `_format_prior_entity_context` (§2).
