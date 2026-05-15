## Wiki quality: phantom characters, stale relationships, incoherent timeline

### Assessment
Full 344-turn extraction produced 155 wiki pages. Template and provenance tracking are excellent, but data quality issues make the wiki unreliable as a player reference. Overall rating: 2.5/5.

### Critical Issues

1. **12+ phantom characters** that are actually abilities/concepts (char-echo, char-pattern, char-weave, char-field, char-precision, char-quiet, char-triangular, char-disruption, char-broken, char-chief, char-moonwind, char-song). The compound ability "Triangular Pattern Disruption Field" was shattered into 4 separate character entities.

2. **Massive relationship staleness**: char-player has 121 relationships, nearly all marked `active`. Player is still listed as "captive of Two figures" and "bound by bindings" from turn 1, 300+ turns later. No relationship lifecycle management (active → resolved).

3. **Location normalization failures**: loc-the-ground, loc-ground (duplicates); loc-the-area-beneath-him, loc-morning-sky, loc-the-edge (not real locations); loc-fenouille (player name became location).

4. **Player character duplication**: char-player and char-fenouille-moonwind are the same person with separate entities.

5. **Incoherent season timeline**: Seasons oscillate wildly (Mid Winter → Early Spring → Mid Winter → Mid Summer → Mid Winter). Temporal signal extractor picks up seasonal descriptions rather than tracking progression.

6. **Kael incorrectly titled "Shaman"**: char-kael has title=Shaman at turn 256, but Kael is a warrior/hunter. The Shaman is char-shaman.

### What's Missing
- No narrative history section on entity pages (only current state)
- No wiki landing page / world overview
- No event cross-references on entity pages (398 events exist but aren't linked)
- No open questions / unresolved mysteries section

### Proposed Improvements
- Relationship lifecycle: mark relationships as resolved/historical when context changes
- Location validation: reject vague/generic phrases as locations  
- Player character alias consolidation
- Temporal signal coherence checking (seasons can only move forward)
- Entity history timeline on wiki pages
