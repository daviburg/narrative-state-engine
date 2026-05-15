# B70 175-Turn Extraction Quality Assessment

## Source
- Framework: `framework-local/ab-test/b70-full-a/`
- Model: Qwen 3 8B INT4 (OpenVINO, dual Arc Pro B70)
- Turns: 1-175 (incremental 25-turn batches)
- Date: 2026-05-11

## Entity Counts
- Characters: 23
- Locations: 19
- Items: 43
- Factions: 7
- Total: 93 (after 175 turns)

## Human Assessment (by user)

### Locations

| Issue | Entity | Verdict | Action |
|---|---|---|---|
| Body part as location | `loc-lips` ("his lips") | **Garbage** — clear misclassification | Remove |
| Verbose backdrop | `loc-arctic` | **Acceptable** — backdrop for the story, not a bug | Keep |
| Concept plans | `loc-fighting-place`, `loc-hiding-place` | **Acceptable** — player does civilization building, plans become real places | Keep |
| Overlap: shelters/home/longhouse | `loc-shelters`, `loc-communal-home`, `loc-longhouse` | **Duplicates** — all the same structure at different stages | Merge |
| Overlap: arctic settings | `loc-arctic`, `loc-arcticwild` | **Duplicates** — same setting | Merge |
| Overlap: camp/lean-tos | `loc-camp`, `loc-snow-lean-tos` | **Duplicates** — lean-tos are part of the camp | Merge or parent-child |

### Characters

| Issue | Entity | Verdict | Action |
|---|---|---|---|
| Duplicate elder | `char-elder`, `char-eldorman` | **Duplicate** — same person | Merge |
| PC alias fork | `char-fenouille-moonwind` | **Unwanted fork** — this is the player character! | Merge into char-player |
| Generic groups | `char-men`, `char-women`, `char-females`, `char-group`, `char-villagers`, `char-children`, `char-hunters` | **Acceptable for now** — story does have recurring generic groups | Keep, reassess later |

### Items

| Issue | Verdict | Action |
|---|---|---|
| Noisy scene props | Items like `coldwater`, `meltedsnow`, `usedbowlsutensils`, `woodendishes`, `bowl`, `ladle` | **Too noisy** — need optimization for meaningful signal | Future improvement |

### Factions

| Verdict | Action |
|---|---|
| Mostly clean | Not touching for now |

## Key Findings for Pipeline Improvement

1. **Body part as location** (`loc-lips`) — the `_is_misclassified_location` filter didn't catch this. Needs investigation.
2. **PC alias fork** (`char-fenouille-moonwind`) — the PC alias merge (`_merge_pc_aliases`) failed to catch this. The name "Fenouille Moonwind" should have been identified as a PC alias.
3. **Same-turn entity duplication** (`char-elder`/`char-eldorman` both first_seen turn-016) — the dedup pass didn't catch these semantically similar names.
4. **Location duplication across turns** (shelters/communal-home/longhouse) — same physical location described differently as it evolves. Hard problem: these ARE different descriptions at different narrative stages.
5. **Item noise** — too many ephemeral scene props extracted. Need better discrimination of narratively significant items.
