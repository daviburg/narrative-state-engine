# Synthesis Layer Design — Issues #109 & #110

Narrative wiki synthesis and relationship arc summarization: transforming raw extraction data into human-readable character pages, location histories, and entity biographies.

**Prerequisites**: Read [design-entity-pipeline-v3.md](design-entity-pipeline-v3.md) first. This design builds on those findings.

---

## Table of Contents

1. [Target Output Examples](#1-target-output-examples)
   - 1.1 What exists today
   - 1.2 Ideal character page: Fenouille Moonwind (refined)
   - 1.3 Ideal event-only character page: Kael (refined)
   - 1.3a Ideal character page: The Elder
   - 1.3b Ideal location page: The Settlement
   - 1.4 What changed from the current pages
   - 1.5 Critical Event Inventory
   - 1.6 Information Loss Taxonomy
   - 1.7 Personality and Desire Analysis
2. [Relationship Arc Design](#2-relationship-arc-design)
3. [Narrative Generation Design](#3-narrative-generation-design)
4. [Events-First Architecture](#4-events-first-architecture)
5. [Pipeline Architecture](#5-pipeline-architecture)
6. [Entity Type Adaptations](#6-entity-type-adaptations)
7. [Open Questions](#7-open-questions)
8. [Implementation Plan](#8-implementation-plan)

---

## 1. Target Output Examples

### 1.1 What exists today

Current wiki pages are template-rendered JSON dumps. `char-player.md` contains:
- A flat infobox (race, class, first_seen)
- `current_status` frozen at turn-054 ("Leaning against the brave warrior and sharing warmth")
- 26 relationship rows in an undifferentiated table
- 11 relationship history sections listing micro-interactions turn by turn

This tells a reader *what data was extracted*, not *what happened to this character*. The same character actually lived through 345 turns: awakening in the snow, being captured by strangers, joining a tribe, falling in love, becoming a leader, bearing children, building a civilization, and navigating complex alliances — none of which is apparent from the current page.

### 1.2 Ideal character page: Fenouille Moonwind (char-player)

*Refined April 2026 after transcript validation. See §1.5 for the critical event inventory and §1.6 for the information loss taxonomy that informed these changes.*

```markdown
# Fenouille Moonwind

> Elven warlock who awakened alone in the snow, was accepted into a band of
> migrating hunter-gatherers, and over 345 turns transformed them into the
> Quiet Weave — a settled proto-village built on intentional pattern-making,
> empirical observation, and balanced governance. Mother of four children,
> partner to Kael, and architect of a civilization.

| | |
|---|---|
| **Race** | Elf |
| **Class** | Warlock |
| **First Seen** | turn-001 |
| **Last Updated** | turn-344 |
| **Status** | Leading the Quiet Weave; pregnant with fourth child; balancing structured order against the "song of chaos" |

## Biography

### The Awakening (turns 1–14)

Fenouille regained consciousness face-down in the snow, head throbbing, fragments
of a mission surfacing — something stolen, a Midwinter celebration in jeopardy
[turn-001]. Following footprints into the forest, she triggered a snare that
injured her shoulder [turn-005] and was captured by two figures who bound her in
icy vines and marched her deeper into the trees [turn-007, turn-009].

### Acceptance into the Tribe (turns 15–55)

Brought to a rough encampment of crude lean-tos around a central bonfire,
Fenouille faced the elder — a grizzled man with eyes like chips of flint
[turn-015]. Through gesture and ritual, she applied a dark fibrous material to
her forehead and was accepted: the net was removed, broth offered, strength
restored [turn-015, turn-021, turn-029]. She earned trust by hauling logs,
sorting herbs, and identifying moonpetal, frost-bite balm root, and winter's
breath among the dried bundles [turn-034, turn-039]. The young warrior — a broad
figure clad in white furs, later revealed to be Kael — became her first companion
at the fire [turn-009, turn-050, turn-051].

### Building Bonds and Arcane Beginnings (turns 56–114)

Fenouille and Kael's bond deepened from shared warmth and fire-tending to open
affection: she prepared supply bundles before his hunts and laid a good-luck kiss
on his cheeks [turn-079]. She sought the shaman and began using subtle arcane
power to aid the tribe — sharpening needles, softening hides [turn-059]. The
shaman observed with a flinty gaze and offered a slow, thoughtful nod, accepting
her contribution [turn-059, turn-082, turn-083].

A pivotal moment came when Fenouille chose to stay with the tribe rather than
pursue her original quest. "I could pursue a stolen item which name I can't even
remember, or grow a life with a hunter who loves me and a tribe that values me"
[turn-114]. This marked the definitive shift from external adventurer to
community-builder.

### Revolution: The Longhouse (turns 121–134)

Fenouille discovered she was pregnant [turn-121] and shared the news with Kael
[turn-122, turn-123]. This catalyzed her most transformative initiative: the
construction of the tribe's first permanent communal dwelling. She designed the
longhouse with a central hearth, sleeping alcoves, food storage, and a cradle
space [turn-124], etched blueprints into the snow, and directed labor using
cantrips and protective energies [turn-127]. She laid a split-log floor [turn-128],
designed a steep-pitched roof with wood shingles and moss-and-clay insulation
[turn-130], and announced the completion of the building envelope [turn-132].
Kael completed the central fire pit with smooth stones and specialized clay
[turn-133]. The shaman drew a protective rune above the entrance [turn-133].

This was arguably the story's defining turning point: Fenouille transformed from
a helpful tribesmember into a bringer of revolutionary change. The longhouse
replaced the crude lean-tos and simple tents [turn-047] and enabled the shift
from migrating hunter-gatherers to a settled community.

### Sedentarization and First Children (turns 135–183)

With shelter secured, Fenouille launched a program of civilizational change. She
initiated proto-agriculture — planting edible vegetation near the longhouse,
clearing nut and berry trees, teaching ice fishing, and demonstrating concepts of
seed cultivation [turn-136, turn-137]. She prepared names blending her heritage
and Kael's [turn-138], and **Lyrawyn** was born — a girl with fair skin and
eyes holding a depth suggesting ancient knowledge [turn-141]. Fenouille brought
her to breast and requested the shaman's blessing [turn-142, turn-143].

She invented mead from wild honey and purified snowmelt [turn-145, turn-147],
ventured out to gather greens for sauerkraut [turn-149], and discovered arctic
dock, fireweed shoots, and wild mustard for fermentation [turn-151]. These food
preservation techniques — mead, sauerkraut, fermented greens — fundamentally
changed the tribe's survival capacity [turn-153].

She recruited Lena as co-teacher and began teaching counting through songs, rhythm,
and dance [turn-157, turn-161]. She designed defensive measures around the
longhouse: cleared brush, sound traps, and a basic perimeter [turn-166, turn-167].
She discussed water reserves and safe havens with Kael [turn-169].

The first tentative harvest of cultivated arctic dock and fireweed marked a
triumph [turn-173]. She then introduced pottery — constructing a small
experimental kiln, firing the tribe's first ceramic vessels [turn-177]. A larger
communal kiln followed under her supervision, with Borin selected as apprentice
[turn-178, turn-179].

**Faelan** was born — a son whose arrival catalyzed a surge of arcane power in
Fenouille, expanding her magical potential and deepening her connection to the
land [turn-183]. She gained the ability to understand beasts and perceive hidden
magical auras [turn-186, turn-187].

### Discovery and Crisis (turns 184–260)

Fenouille detected a faint trail of magical discharge near the eastern boundary
[turn-189] and taught Anya to sense subtle energies [turn-191]. She placed her
hand on the snow and fed pact-power into the residue; a distant construct
responded with blue-white flashes and mountain-thrumming vibrations [turn-192,
turn-193]. She appointed Kael as acting leader and departed on a 7–10 day eastern
expedition with Rurik [turn-194, turn-195], discovering hexagonal melt patterns,
crystalline growths, dark fragments, and an invisible boundary affecting
vegetation [turn-201, turn-202]. She brought a fragment back to camp, wrapping it
in cloth and marking a reference circle toward the mountains [turn-204].

A sick and weakened traveler arrived at the longhouse [turn-225] — the beginning
of a plague crisis. Fenouille established rigorous isolation protocols [turn-232,
turn-233], used arcane sight to reveal a structured pattern of illness (not
natural but engineered) [turn-240, turn-241], and conducted methodical disruption
experiments [turn-238, turn-242, turn-243]. Working with Anya, the shaman, and
the healer, she developed the Pattern Disruption Method — interrupting the
sickness at its leading edge using precise frost-line techniques [turn-247,
turn-248, turn-251]. The survivor recovered fully [turn-252].

She codified this into a teachable protocol, assigned formal roles (healer for
recognition, shaman for rites, Anya for precision, Kael for boundary), and sent
a diplomatic delegation bearing diagrams and knowledge to the southern tribes
[turn-252, turn-253].

### The Fragment and the Disruption Fields (turns 260–290)

Fenouille led an expedition to the pulsating river stones the traveler had
described [turn-268, turn-270]. She instructed Anya to trace a minimal frost
interruption at the outer boundary [turn-271], creating a local reaction in the
crystalline growths [turn-272]. Through systematic experimentation she discovered
that a triangular arrangement of three disruption points forced the system to
divide its correction — the Triangular Pattern Disruption Field [turn-277,
turn-278]. She confirmed repeatability [turn-280], committed the method to
inventory [turn-282], and withdrew without leaving a trace [turn-284].

Back at the settlement, two triangular fields were established for protection,
with Kael overseeing the perimeter and Anya placing precise disruptions [turn-285,
turn-286]. She addressed the council on future plans beyond mere survival
[turn-287, turn-288].

### Naming the Quiet Weave (turns 290–305)

Fenouille declared the settlement's purpose and philosophy, emphasizing clarity
over strength, observation over reaction, and knowledge that could be passed
forward without losing shape. She formally named it "The Quiet Weave" [turn-291,
turn-292]. She walked each leader through hands-on examples of observational
methodology and established reciprocal exchange with southern visitors [turn-293,
turn-294].

She deliberately stepped back for weeks, letting systems run independently, then
returned to measure what remained without her [turn-295, turn-296]. She formally
delegated: land to Kael, learning to Lena, healing to the healer, the unseen to
the shaman [turn-301]. She called a final assessment council before withdrawing
to the longhouse for the birth [turn-305].

### Birth of Rune and Expansion (turns 306–330)

**Rune** was born within the longhouse — a child with a miniature snowflake
pulsing in her eyes, her aura a clean mathematical purity aligned with the Quiet
Weave's principles. The fragment emitted a joyous flare of blue-white light;
Lyrawyn gasped as a frost pattern formed and dissolved at her feet [turn-306].
Fenouille established strict conditions for Rune's protection: never within a
disruption field, not brought near the fragment, always observed in both
structured and unstructured environments [turn-307, turn-308].

In autumn, food stores exceeded expectations — enough for two winters [turn-312].
Fenouille **conceived a fourth child** as a reaffirmation of continuity
[turn-312]. She called an assembly of leaders and invited Chief Thorne, Elder Lyra,
and Warrior-Chief Gorok to the settlement's boundary [turn-313, turn-314]. She
offered Thorne food without conditions [turn-316], explained the principles of
harmony to Lyra [turn-316], and discussed strategic alliance with Gorok [turn-316].

Lyra pledged to be Lyrawyn's godmother [turn-324]. Borin accepted Faelan's
tutelage [turn-324]. Walking with Kael, Fenouille guided his hand to feel the
fourth child's movement: "Your strength, your story lives strong in our children"
[turn-326].

### The Balance of Order and Chaos (turns 330–344)

Maelis of the Swift Arrows arrived — a hunter-scout from the Riverfolk with
unmatched bow precision and a strategic mind [turn-332]. Fenouille welcomed her
and assigned integration tasks [turn-333], seeing in her the "unpatterned"
quality the settlement increasingly lacked.

In the final winter council, Fenouille methodically questioned every member
[turn-339]. Kael reported that patterned hunts were easier but drew predators;
Tala warned that outsiders saw the patterns as a cage; Gorok admitted that
warriors hesitated when demanding unthinking aggression; the Elder delivered his
defining wisdom: "I see patterns of what was and what forgets. Danger is we
become deaf to the song of chaos... Maelis carries that song in her bones. Do
not silence it" [turn-340].

Fenouille affirmed Maelis: "What you named is what we stopped seeing." She
framed Gorok and Maelis as complementary forces — "my right and my left hand.
Each acts when the other should not" — and orchestrated a final equilibrium of
structure and wildness, cohesion and individual authority [turn-343, turn-344].

## Key Relationships

### Kael (the young warrior / char-broad-figure)
**Arc: Stranger → Companion → Partner → Co-leader**

First encountered as a silent figure clad in white furs [turn-009]. Their bond
grew from shared warmth at the fire [turn-050, turn-051] to affection [turn-079]
and intimacy [turn-108]. Kael became Fenouille's trusted partner in leadership:
acting leader during her eastern expedition [turn-194], perimeter commander for
the disruption fields [turn-286], and strategic advisor in winter council
[turn-340]. They have four children together: Lyrawyn, Faelan, Rune, and one
still in utero. By turn-344, Kael independently identifies "what would hold
without the patterns: family bonds, old stories, clean water, shelter."

### The Elder (char-elder)
**Arc: Captor's Authority → Mentor → Spiritual Conscience**

Commanded Fenouille's acceptance ritual with stern authority [turn-015, turn-019].
Read her character and assigned roles based on observed aptitude [turn-019].
Over time became a quiet observer, offering subtle approval through nods. By
turn-340, frail but sharp-minded, he serves as the tribe's moral anchor:
"You see patterns of what is and what can be made... I see patterns of what was
and what forgets." He warns Fenouille that the settlement risks becoming deaf to
chaos and instructs her not to silence Maelis [turn-340].

### Lyrawyn (first daughter)
**Arc: Infant → Pattern-Sensitive Child**

Born turn-141. Blessed by the shaman with endurance and protection [turn-143].
By turn-324 she shows deepening pattern attunement, teaching other children
"rightness" and providing intuitive breakthroughs in the Pattern Language of the
Land. Lyra becomes her godmother [turn-324].

### Faelan (first son)
**Arc: Infant → Materials Apprentice**

Born turn-183. Under Borin's tutelage, Faelan sees unseen stresses in wood and
stone, can point to weakness before it is tested, and is developing simplified
construction schematics [turn-324, turn-332].

### Rune (third child)
**Arc: Newborn → Embodiment of the Quiet Weave**

Born turn-306 with inherent arcane alignment — a snowflake pulsing in her eyes,
an aura of clean mathematical purity. Objects near her exhibit geometric clarity;
animals seek spaces she occupied [turn-306, turn-332]. Fenouille's immediate
response was clinical: testing whether Rune was child or function [turn-307].

### Anya
**Arc: Student → Arcane Precision Specialist**

Trained in energy sensing [turn-191] and frost-line discipline. By turn-286,
places precise disruption fields independently. By turn-340, understands that
patterns "reshape themselves in ways we could not perceive."

### Lena
**Arc: Recruit → Education Leader**

Recruited as co-teacher [turn-161]. Manages children's education and cultural
preservation. Reports with pride on children's capacity for creative variation
[turn-306, turn-332].

## Personality

| Trait | Evidence | Key Turns |
|---|---|---|
| **Strategic intelligence** | Plans multi-season agricultural experiments, establishes council hierarchy, designs longhouse from blueprint | turn-108, turn-124, turn-210 |
| **Charismatic leadership** | Persuades tribe through demonstration, delegates authority effectively, inspires loyalty in outsiders | turn-137, turn-301, turn-315 |
| **Civilization-building drive** | Introduces longhouse, agriculture, pottery, education, governance — each building on the last | turn-124, turn-136, turn-177, turn-291 |
| **Methodical empiricism** | Tests before deploying (plague protocol, disruption fields, harvest experiments) | turn-238, turn-271, turn-295 |
| **Controlled response to the unknown** | "We do not ignore what we did not intend" — embraces unintended consequences as data | turn-149, turn-307 |

**Primary motivation:** Build a permanent, self-sufficient civilization grounded in empirical understanding — "observation over guessing, testing over reaction, clarity over strength" [turn-291].

**Interpersonal style:** Leads through demonstration and delegation rather than command. By turn-301, explicitly steps back to test whether the system stands without her.

## Current Status

*As of turn-344:*

Leading the Quiet Weave settlement through deep winter. Pregnant with fourth
child. Managing a council of specialists: Kael (scouting/defense), Lena
(education), Borin (construction), Gorok (perimeter/unknown threats), Maelis
(chaos/adaptation), Lyra and Anya (arcane research), the healer (remedies), the
shaman (spiritual guidance). Balancing the tension between structured order and
the "song of chaos" the Elder warns must not be silenced. Maintaining diplomatic
relations with Thorne's people; integrating newcomers (Renn, Joric, Elara, Bran).
Fourth child still in utero.

## Attributes

| Trait | Value | Source |
|---|---|---|
| Race | Elf | turn-019 |
| Class | Warlock | turn-019 |
| HP Change | -2 HP lost (restored by +4) | turn-031 |
| Arcane Abilities | Speak with Animals, Perceive Magical Auras, Frost Precision (via Anya) | turn-186, turn-187, turn-266 |
| Pact | Celestial (the Silent Star) | turn-083, turn-304 |

## Appendix: Event Timeline

| Turn | Event | Significance |
|---|---|---|
| turn-001 | Awakens in the snow, recalls Midwinter mission | — |
| turn-005 | Triggers snare, injures shoulder | — |
| turn-007 | Captured by two figures with icy vines | — |
| turn-015 | Brought to encampment, acceptance ritual begins | CRITICAL |
| turn-029 | Released from net, offered broth, HP restored | — |
| turn-034 | Begins helping with logs, earning trust | — |
| turn-039 | Identifies herbs: moonpetal, frost-bite balm root | — |
| turn-059 | Approaches shaman, begins using arcane power openly | MAJOR |
| turn-079 | Gives supply bundle and kiss to young hunter | — |
| turn-083 | Senses shaman's earthy magic, feels celestial pact connection | MAJOR |
| turn-114 | **Chooses to stay with the tribe** | CRITICAL |
| turn-121 | Discovers pregnancy | MAJOR |
| turn-124 | Designs and begins longhouse construction | CRITICAL |
| turn-132 | Longhouse building envelope completed | CRITICAL |
| turn-136 | Initiates sedentarization program (agriculture, fishing) | CRITICAL |
| turn-141 | **Lyrawyn born** (first child, daughter) | CRITICAL |
| turn-145 | Invents mead from wild honey | MAJOR |
| turn-153 | Successfully creates sauerkraut (food preservation) | MAJOR |
| turn-157 | Begins teaching counting through song and dance | MAJOR |
| turn-161 | Recruits Lena as co-teacher | MAJOR |
| turn-166 | Designs defensive perimeter around longhouse | — |
| turn-173 | First harvest of cultivated arctic dock | CRITICAL |
| turn-177 | Constructs first kiln; fires ceramic vessels | CRITICAL |
| turn-179 | Apprentices Borin in kiln mastery | — |
| turn-183 | **Faelan born** (second child, son); arcane power expands | CRITICAL |
| turn-189 | Detects arcane trail at eastern boundary | MAJOR |
| turn-194 | Appoints Kael acting leader, departs east | MAJOR |
| turn-201 | Discovers hexagonal anomalies, crystalline growths, fragments | MAJOR |
| turn-225 | Sick traveler arrives — plague crisis begins | CRITICAL |
| turn-252 | Plague resolved; protocol codified and shared with southern tribes | CRITICAL |
| turn-277 | Invents Triangular Pattern Disruption Field | CRITICAL |
| turn-286 | Deploys two triangular fields for settlement defense | MAJOR |
| turn-291 | Names settlement "The Quiet Weave"; declares philosophy | CRITICAL |
| turn-301 | Formally delegates leadership; steps back | CRITICAL |
| turn-306 | **Rune born** (third child); arcane alignment in child | CRITICAL |
| turn-312 | Conceives fourth child | MAJOR |
| turn-314 | Receives Thorne, Lyra, Gorok at settlement boundary | MAJOR |
| turn-326 | Kael feels fourth child's movement | — |
| turn-340 | Winter council: all factions report; Elder warns about chaos | MAJOR |
| turn-343 | Affirms Maelis; balances Gorok and Maelis as dual forces | CRITICAL |
| turn-344 | Final assignments; settlement enters mature phase | — |
```

### 1.3 Ideal event-only character page: Kael (no catalog entry)

Kael has 16 events but zero catalog entry. This demonstrates the design working with events as the sole data source.

*Refined April 2026 with personality traits and independent motivations added.*

```markdown
# Kael

> Hunter, scout, and partner to Fenouille. Acting leader of the Quiet Weave
> during Fenouille's absence. Father of Lyrawyn, Faelan, Rune, and a fourth
> child in utero. Stoic, observant, and profoundly devoted — expresses love
> through service and presence rather than words.

| | |
|---|---|
| **Type** | Character |
| **First Event** | turn-169 |
| **Last Event** | turn-343 |
| **Status** | Managing perimeter defense and scouting; partner and co-leader |
| **Data Source** | Events only (no catalog entry) |

## Biography

### Early References (turn-108 onward)

Kael first appears by name when Fenouille turns to him with a directive about the
southern tribes [turn-108]. He is the young warrior who has been Fenouille's
companion since the early days of the settlement, referenced earlier as
"the young warrior" and "the broad figure."

*Note: `char-broad-figure` in the entity catalog likely refers to the same
person before he was identified by name. This connection has not been confirmed
by the extraction pipeline.*

### Trusted Lieutenant (turns 169–210)

Fenouille discusses water barrels and safe havens with Kael, emphasizing
preparedness [turn-169]. When she departs on an eastern expedition, she appoints
Kael as acting leader of the settlement [turn-194, turn-195]. She asks him to
select two calm-tempered hunters for diplomatic gift preparation and migration
path mapping [turn-210]. In a quiet moment, she sits beside him in the longhouse,
expressing trust and closeness [turn-210].

### Perimeter and Defense (turns 286–301)

Kael oversees the perimeter as Fenouille establishes triangular disruption fields
[turn-286]. She instructs Kael, Lena, and the elders on observational methods
[turn-293, turn-294]. As Fenouille steps back from direct leadership, Kael takes
on greater autonomous responsibility [turn-295, turn-301].

### Fatherhood and Late-Game (turns 306–343)

Kael's fourth child grows within Fenouille; he feels its movement and hears her
affirmation: "Your strength, your story lives strong in our children" [turn-326].
He agrees to seek out all-female warrior tribes despite it challenging his
traditional views [turn-328]. In winter council, Kael reports on animal movement
patterns and territorial observations: hunts are easier in patterned zones but
draw predators to the edges [turn-339, turn-340]. When asked what would hold
without the patterns, he identifies the essentials: family bonds, old stories,
clean water, shelter [turn-344]. He is tasked with finding a boundary with Lyra
and Bran regarding the spreading pattern [turn-343].

## Personality

| Trait | Evidence | Key Turns |
|---|---|---|
| **Stoic loyalty** | Silent communication through nods; expresses feelings through presence, not words | turn-051, turn-210 |
| **Practical grounding** | Identifies "what holds without the patterns" as core survival elements | turn-344 |
| **Adaptive trust** | Accepts Fenouille's unconventional requests (seeking warrior women, strategic polygamy discussion) despite personal discomfort | turn-326, turn-328 |
| **Growing strategic thinking** | Evolves from silent companion to independent analyst of territorial patterns | turn-340, turn-344 |

**Primary motivation:** Family lineage and tribe strength. Deep devotion to Fenouille expressed through service and deference to her judgment.

## Key Relationships

### Fenouille (char-player)
**Arc: Companion → Partner → Co-leader**

Romantic and leadership partner. Co-parent of Lyrawyn, Faelan, Rune, and a
fourth child in utero. Kael serves as Fenouille's primary military and scouting
advisor, and the person she trusts with full settlement authority in her absence.

### Tala
**Arc: Fellow Council Member → Mission Partner**

Paired with Kael for the diplomatic mission to recruit allies [turn-329, turn-330].

### Maelis
**Arc: New Counterpart**

Kael initially brought Maelis to the settlement; by turn-343, they are framed
as complementary forces under Fenouille — Gorok and Maelis as dual anchors.

## Current Status

*As of turn-344:*

Managing perimeter defense and animal movement tracking for the Quiet Weave.
Tasked with finding with Lyra and Bran where the pattern must NOT grow.
Fourth child with Fenouille still in utero. Reports steady effectiveness of
patterned hunting zones but emerging edge predator risks.

## Appendix: Event Timeline

| Turn | Event |
|---|---|
| turn-169 | Discusses water barrel and safe haven with Fenouille |
| turn-194 | Appointed acting leader during Fenouille's eastern expedition |
| turn-195 | Receives detailed leadership plan from Fenouille |
| turn-210 | Selects hunters for gifts; shares closeness with Fenouille |
| turn-286 | Oversees perimeter for triangular field establishment |
| turn-293 | Instructed on observational methods |
| turn-294 | Instructed on learning from the land's purpose |
| turn-295 | Evaluated on sustainability of assigned tasks |
| turn-301 | Receives delegated responsibilities as Fenouille steps back |
| turn-326 | Feels fourth child's movement; hears Fenouille's affirmation |
| turn-328 | Agrees to seek all-female warrior tribes |
| turn-329 | Assigned recruitment mission with Tala |
| turn-330 | Mission parameters refined |
| turn-339 | Reports to winter council |
| turn-340 | Reports on animal movement patterns and perimeter |
| turn-343 | Tasked with boundary work with Lyra and Bran |
| turn-344 | Identifies "what would hold without patterns" |

*Note: Events before turn-169 may exist under the ID `char-broad-figure`.
See entity resolution notes.*
```

### 1.3a Ideal character page: The Elder (char-elder)

*New target page addition — captures a character who appears throughout the story but has minimal event data, relying heavily on narrative voice.*

```markdown
# The Elder

> Grizzled patriarch and spiritual conscience of the tribe. The Elder never
> leads through command but through ritual, symbol, and carefully timed
> wisdom. He is the memory of what came before and the warning against
> forgetting it.

| | |
|---|---|
| **Type** | Character |
| **First Event** | turn-017 |
| **Last Event** | turn-340 |
| **Status** | Frail but sharp-minded; serving as moral anchor of the Quiet Weave |
| **Data Source** | Events + catalog |

## Biography

### Authority and Acceptance (turns 9–29)

The elder first appears as the tribe's commanding presence — "eyes like chips of
flint" — dismissing Fenouille's attempts at communication in known languages
[turn-009, turn-017]. He examined her warlock attire and elven features before
offering dark fibrous material for the acceptance ritual [turn-019]. He
communicated through subtle gestures: a nod to loosen bonds, a wave to indicate
the material should be consumed [turn-021, turn-027]. After Fenouille applied the
material, he signaled acceptance — the net was removed and broth offered
[turn-029].

### Observer and Role-Assigner (turns 34–47)

The elder reads character through observation. He gestured Fenouille toward herb
sorting and offered her a carving tool, assigning tasks based on her "lighter
touch" and apparent aptitude rather than tribe hierarchy [turn-037]. He signals
the communal meal [turn-043] and departs to a larger, more enclosed structure
[turn-047] — always present but separate from the daily labor.

### Spiritual Conscience (turns 290–340)

By the late game, the Elder endures deep winter though his body is frailer. He
remains a silent anchor among council members. His defining moment comes in the
winter council when, asked what he sees, he delivers the story's moral center:
"You see patterns of what is and what can be made... I see patterns of what was
and what forgets. Danger is we become deaf to the song of chaos, the wildness
that reminds us of the true shape of things, beyond our own making. Maelis
carries that song in her bones. Do not silence it" [turn-340].

## Personality

| Trait | Evidence | Key Turns |
|---|---|---|
| **Ritualist** | Uses physical markers and symbolic gestures rather than speech | turn-019, turn-029 |
| **Character reader** | Assigns roles by observed aptitude, not hierarchy | turn-019, turn-037 |
| **Spiritual guardian** | Protects "why" beneath "how"; warns against hubris of pure order | turn-340 |

**Primary motivation:** Preservation of memory and the essential wildness that precedes human pattern-making.

## Current Status

*As of turn-340:*

Frail, enduring deep winter. Serving as the tribe's spiritual anchor and moral
conscience. His authority is not administrative but philosophical — he speaks
rarely, and when he does, Fenouille listens.
```

### 1.3b Ideal location page: The Settlement / The Quiet Weave

*New target location page — demonstrates the settlement's physical transformation as a structural narrative.*

```markdown
# The Settlement (The Quiet Weave)

> From crude lean-tos around a bonfire to a structured proto-village with
> longhouse, kilns, cultivation plots, disruption fields, and formalized
> governance — the settlement's physical evolution mirrors the story's
> central arc.

| | |
|---|---|
| **Type** | Location |
| **First Event** | turn-007 |
| **Last Event** | turn-344 |
| **Status** | Named settlement with ~50+ inhabitants, inter-tribal connections, formalized knowledge systems |

## Evolution

### Primitive Camp (turns 7–120)

Initially a rough encampment of crude lean-tos and simple tents around a central
bonfire [turn-007, turn-047]. Hunters wore rough furs and sharpened spears by
firelight. Women scraped hides and sorted herbs. The tribe sheltered under simple
structures and dispersed to lean-tos after communal meals [turn-047].

### Longhouse Era (turns 124–170)

Fenouille designed and built the tribe's first permanent structure: a communal
longhouse with split-log floor, post-and-beam walls, steep-pitched shingled
roof, moss-and-clay insulation, central fire pit with smoke hole, sleeping
alcoves, food storage, and a birthing chamber [turn-124–turn-134]. The shaman
placed a protective rune above the entrance [turn-133]. This single structure
enabled sedentarization.

### Agricultural Settlement (turns 136–210)

Cultivation plots established near the longhouse — arctic dock, fireweed,
wild mustard, nut and berry trees [turn-136, turn-173]. Drying racks and two
kilns built for food preservation and pottery [turn-177, turn-178, turn-210].
Defensive perimeter designed: cleared brush, sound traps, palisade elements
[turn-166, turn-167]. Water barrel and fire safety reserves placed outside
[turn-168, turn-169].

### The Quiet Weave (turns 291–344)

Formally named and philosophically anchored by Fenouille [turn-291, turn-292].
Two triangular disruption fields deployed for protection [turn-285, turn-286].
Council structure formalized with delegated authority [turn-301]. Multiple
habitation clusters planned [turn-332]. Pattern Language of the Land documented
in scrolls [turn-332]. Inter-tribal inhabitants arrive from Thorne's people
[turn-324, turn-332].

## Key Events at This Location

| Turn | Event | Category |
|---|---|---|
| turn-007 | Fenouille brought to camp as captive | Origin |
| turn-047 | Dispersal to lean-tos after meal (primitive state) | Baseline |
| turn-124 | Longhouse construction begins | CRITICAL |
| turn-132 | Longhouse building envelope completed | CRITICAL |
| turn-136 | Agriculture program initiated | CRITICAL |
| turn-141 | Lyrawyn born in longhouse | Milestone |
| turn-173 | First harvest of cultivated plants | CRITICAL |
| turn-177 | First kiln constructed; ceramic vessels fired | CRITICAL |
| turn-183 | Faelan born | Milestone |
| turn-225 | Sick traveler arrives — plague crisis | Crisis |
| turn-252 | Plague resolved; knowledge codified | Resolution |
| turn-285 | Triangular disruption fields deployed | Defense |
| turn-291 | Settlement named "The Quiet Weave" | CRITICAL |
| turn-301 | Distributed governance adopted | CRITICAL |
| turn-306 | Rune born with arcane alignment | Milestone |
| turn-314 | Outside leaders (Thorne, Lyra, Gorok) visit | Diplomacy |
| turn-332 | Newcomers integrated; winter report | Expansion |
| turn-340 | Full winter council — all factions | Governance |

## Connected Entities

| Entity | Connection |
|---|---|
| char-player (Fenouille) | Founder, leader, architect |
| char-elder | Spiritual anchor |
| char-kael | Perimeter defense, acting leader |
| char-lena | Education leader |
| char-borin | Construction specialist |
| char-anya | Arcane defense (disruption fields) |
| char-gorok | Warrior integration |
| char-maelis | Chaos/adaptation counterbalance |
| faction-chief-thorne | Allied external tribe |
```

### 1.4 What changed from the current pages

| Aspect | Current | Original Target (v1) | Refined Target (v2) |
|---|---|---|---|
| **Structure** | Flat data dump | Narrative biography with phases | Narrative biography with phases + personality section |
| **Relationships** | 26 rows of micro-interactions | 4–5 arc summaries | 6–7 arcs including children as named entities |
| **Children** | Not mentioned | Rune mentioned; Lyrawyn/Faelan in passing | All four children with individual arcs |
| **Civilization arc** | Not present | Not present | Longhouse → agriculture → pottery → governance as central narrative |
| **Crises** | Not present | Not present | Plague crisis and fragment discovery as full arcs |
| **Status** | Frozen at turn-054 | Reflects turn-345 | Reflects turn-344 including fourth pregnancy |
| **Events** | Not included | 14-row timeline | 35+ row timeline with significance tags |
| **Personality** | Not present | Not present | Structured personality table with evidence |
| **Missing entities** | No page at all | Event-derived page | Event-derived page + personality + independent motivations |
| **Locations** | Not present | Not present | Settlement evolution page showing physical transformation |

### 1.5 Critical Event Inventory for char-player

Complete event inventory organized by narrative phase, validated against both the events catalog (283 events, 277 for char-player) and the raw transcript. Tags: `[CRITICAL]` = must appear in biography, `[MAJOR]` = should appear, `[MINOR]` = adds color but can be omitted.

#### Phase: The Awakening (turns 1–14)

- turn-001: Awakens in snow, recalls Midwinter mission `[in events: YES]` `[in target v1: YES]` `[CRITICAL]`
- turn-004: Lost 2 HP continuing along tracks `[in events: YES]` `[in target v1: NO]` `[MINOR]`
- turn-005: Triggers snare, injures shoulder `[in events: YES]` `[in target v1: YES]` `[MAJOR]`
- turn-006: Decides to surrender `[in events: YES]` `[in target v1: NO]` `[MINOR]`
- turn-007: Captured by two figures with icy vines `[in events: YES]` `[in target v1: YES]` `[CRITICAL]`
- turn-009: Bound with net and rope, escorted deeper `[in events: YES]` `[in target v1: YES]` `[MAJOR]`

#### Phase: Acceptance into the Tribe (turns 15–55)

- turn-015: Brought to rough encampment near bonfire `[in events: YES]` `[in target v1: YES]` `[CRITICAL]`
- turn-019: Elder examines warlock attire, offers dark material `[in events: YES]` `[in target v1: YES]` `[CRITICAL]`
- turn-028: Spreads material on forehead (ritual) `[in events: YES]` `[in target v1: YES]` `[MAJOR]`
- turn-029: Released from net, offered broth, HP restored `[in events: YES]` `[in target v1: YES]` `[MAJOR]`
- turn-034: Helps with logs, easing tensions `[in events: YES]` `[in target v1: YES]` `[MAJOR]`
- turn-037: Elder assigns herb sorting via gesture `[in events: YES]` `[in target v1: NO]` `[MINOR]`
- turn-039: Identifies herbs: moonpetal, frost-bite balm `[in events: YES]` `[in target v1: YES]` `[MAJOR]`
- turn-047: Villagers disperse to crude lean-tos `[in events: YES]` `[in target v1: NO — establishes primitive baseline]` `[MAJOR]`
- turn-050: Moves close to young hunter near fire `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-051: Offers friendly smile, rearranges fire logs `[in events: YES]` `[in target v1: YES]` `[MINOR]`
- turn-052: Takes young warrior's hand — first gesture of friendship `[in events: YES]` `[in target v1: NO]` `[MINOR]`

#### Phase: Building Bonds and Arcane Beginnings (turns 56–114)

- turn-059: Attempts self-introduction as "Fenouille Moonwind" `[in events: YES]` `[in target v1: YES]` `[MINOR]`
- turn-059: Approaches shaman, begins using arcane power `[in events: YES]` `[in target v1: YES]` `[CRITICAL]`
- turn-079: Gives supply bundle and kiss to hunter `[in events: YES]` `[in target v1: YES]` `[MAJOR]`
- turn-081: Shaman/medicine person discovered among women `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-082: Attempts connection with shaman, sensing arcane power `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-083: Senses shaman's earthy magic, celestial pact connection `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-094: Decides to stay with hunter, embrace him `[in events: YES]` `[in target v1: NO]` `[MINOR]`
- turn-108: Rejoins hunter ally, strengthens bond, elevates tribal status `[in events: YES]` `[in target v1: YES]` `[MAJOR]`
- turn-114: **Chooses to stay with tribe** over original quest `[in events: YES]` `[in target v1: NO — CRITICAL GAP]` `[CRITICAL]`
- turn-119: Begins using arcane abilities to assist tribe `[in events: YES]` `[in target v1: NO]` `[MAJOR]`

#### Phase: Revolution — The Longhouse (turns 121–134)

- turn-121: Discovers pregnancy `[in events: YES]` `[in target v1: NO — CRITICAL GAP]` `[CRITICAL]`
- turn-122: Informs Kael he will be a father `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-124: Designs and begins longhouse construction `[in events: YES]` `[in target v1: NO — CRITICAL GAP]` `[CRITICAL]`
- turn-126: Decides on durable, insulated communal longhouse `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-127: Etches blueprints in snow, directs labor with cantrips `[in events: YES]` `[in target v1: NO]` `[CRITICAL]`
- turn-128: Lays split log floor, selects timber for walls `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-129: Belly begins to swell visibly `[in events: YES]` `[in target v1: NO]` `[MINOR]`
- turn-130: Designs steep pitch roof with loft `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-132: Announces completion of building envelope `[in events: YES]` `[in target v1: NO — CRITICAL GAP]` `[CRITICAL]`
- turn-133: Fire pit completed; inquires about birthing traditions; shaman's rune `[in events: YES (3 events)]` `[in target v1: NO]` `[MAJOR]`

#### Phase: Sedentarization and First Children (turns 135–183)

- turn-136: Initiates sedentarization: planting, clearing, fishing, trapping `[in events: YES]` `[in target v1: NO — CRITICAL GAP]` `[CRITICAL]`
- turn-137: Articulates vision for cultivation and prosperity `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-138: Prepares for birth, choosing names `[in events: YES]` `[in target v1: NO]` `[MINOR]`
- turn-141: **Lyrawyn born** `[in events: YES]` `[in target v1: NO — CRITICAL GAP]` `[CRITICAL]`
- turn-142: First feed; requests shaman's blessing `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-145: Invents mead from wild honey `[in events: YES]` `[in target v1: NO — MAJOR GAP]` `[MAJOR]`
- turn-147: Perfects mead; offers ceremonial cup to shaman `[in events: YES]` `[in target v1: NO]` `[MINOR]`
- turn-149: Ventures out for cabbage (sauerkraut expedition) `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-151: Discovers arctic dock, fireweed, wild mustard `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-153: **Creates sauerkraut** — food preservation breakthrough `[in events: YES]` `[in target v1: NO — CRITICAL GAP]` `[CRITICAL]`
- turn-155: Discovers second pregnancy `[in events: YES]` `[in target v1: NO — CRITICAL GAP]` `[CRITICAL]`
- turn-157: Begins teaching counting via song and dance `[in events: YES]` `[in target v1: YES]` `[MAJOR]`
- turn-161: Recruits Lena as co-teacher `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-166: Designs defensive perimeter around longhouse `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-173: **First harvest** of cultivated arctic dock `[in events: YES]` `[in target v1: NO — CRITICAL GAP]` `[CRITICAL]`
- turn-177: **Constructs first kiln; fires ceramic vessels** `[in events: YES]` `[in target v1: NO — CRITICAL GAP]` `[CRITICAL]`
- turn-178: Supervises larger kiln, takes Borin as apprentice `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-183: **Faelan born** — son; arcane power expands `[in events: YES]` `[in target v1: NO — CRITICAL GAP]` `[CRITICAL]`

#### Phase: Discovery and Crisis (turns 184–260)

- turn-186: Gains abilities: understand beasts, perceive magical auras `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-189: Detects faint arcane trail at eastern boundary `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-191: Teaches Anya to sense subtle energies `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-193: Vision of ancient alien structure `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-194: **Appoints Kael acting leader; departs east** `[in events: YES]` `[in target v1: YES]` `[CRITICAL]`
- turn-201: Discovers hexagonal anomalies, crystalline growths `[in events: YES]` `[in target v1: NO]` `[CRITICAL]`
- turn-204: Places fragment at center of camp `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-225: **Sick traveler arrives — plague crisis begins** `[in events: YES]` `[in target v1: NO — CRITICAL GAP]` `[CRITICAL]`
- turn-232: Establishes strict isolation protocols `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-240: Arcane sight reveals structured pattern of illness `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-247: Broken Circle ritual disrupts sickness `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-252: **Plague resolved; protocol codified; delegation to south** `[in events: YES]` `[in target v1: NO — CRITICAL GAP]` `[CRITICAL]`

#### Phase: The Disruption Fields (turns 261–290)

- turn-268: Reaches pulsating river stones (expedition destination) `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-271: First controlled test at pattern boundary `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-277: **Invents Triangular Pattern Disruption Field** `[in events: YES]` `[in target v1: NO — CRITICAL GAP]` `[CRITICAL]`
- turn-280: Confirms repeatability `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-282: Method added to inventory `[in events: YES]` `[in target v1: NO]` `[MINOR]`
- turn-285: Two triangular fields deployed at settlement `[in events: YES]` `[in target v1: NO]` `[CRITICAL]`
- turn-286: Kael oversees perimeter; Anya places disruptions `[in events: YES]` `[in target v1: YES]` `[MAJOR]`

#### Phase: Naming the Quiet Weave (turns 290–305)

- turn-291: **Names settlement "The Quiet Weave"; declares philosophy** `[in events: YES]` `[in target v1: NO — CRITICAL GAP]` `[CRITICAL]`
- turn-293: Instructs leaders on observational methodology `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-295: Evaluates sustainability; steps back for weeks `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-301: **Formally delegates leadership; steps back** `[in events: YES]` `[in target v1: YES]` `[CRITICAL]`
- turn-305: Final council assessment before birth `[in events: YES]` `[in target v1: NO]` `[MINOR]`

#### Phase: Birth of Rune and Expansion (turns 306–330)

- turn-306: **Rune born** with arcane alignment `[in events: YES]` `[in target v1: YES]` `[CRITICAL]`
- turn-307: Conditions for Rune's role defined `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-312: **Conceives fourth child** `[in events: YES]` `[in target v1: NO — CRITICAL GAP]` `[CRITICAL]`
- turn-314: Thorne, Lyra, Gorok arrive at settlement boundary `[in events: YES]` `[in target v1: NO]` `[CRITICAL]`
- turn-316: Offers Thorne food; explains principles to Lyra; alliance with Gorok `[in events: YES (3 events)]` `[in target v1: NO]` `[MAJOR]`
- turn-324: Lyra pledges as Lyrawyn's godmother; Borin tutors Faelan `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-326: **Kael feels fourth child's movement** `[in events: YES]` `[in target v1: NO — CRITICAL GAP]` `[CRITICAL]`

#### Phase: The Balance of Order and Chaos (turns 330–344)

- turn-332: Maelis arrives at the Quiet Weave `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-339: Fenouille questions every council member `[in events: YES]` `[in target v1: NO]` `[MAJOR]`
- turn-340: **Winter council: all factions report; Elder warns about chaos** `[in events: YES]` `[in target v1: YES]` `[CRITICAL]`
- turn-343: Affirms Maelis; pairs Gorok and Maelis as dual forces `[in events: YES]` `[in target v1: NO]` `[CRITICAL]`
- turn-344: Final assignments; settlement enters mature phase `[in events: YES]` `[in target v1: NO]` `[MAJOR]`

#### Summary: Inventory Coverage

| Metric | Count |
|---|---|
| Total CRITICAL events identified | 31 |
| CRITICAL events present in target v1 | 8 (26%) |
| CRITICAL events MISSING from target v1 | 23 (74%) |
| Total MAJOR events identified | ~50 |
| MAJOR events present in target v1 | ~6 (12%) |
| Total events in events catalog for char-player | 277 |

### 1.6 Information Loss Taxonomy

Analysis of what categories of information tend to be missed in the target pages, and why.

#### Category 1: Technological/Civilizational Advances

| Subcategory | Events in catalog | In target v1 | Coverage | Why missed |
|---|---|---|---|---|
| Longhouse construction | 19 events (turns 124–160) | 0 | 0% | Multi-turn arc; no single "defining moment" event — spread across 10+ turns of incremental progress |
| Food preservation (mead, sauerkraut, fermentation) | 6 events | 0 | 0% | Domestic/mundane category; not dramatic enough to flag as milestone |
| Pottery/kiln | 3 events | 0 | 0% | Same as above — innovative but not dramatic |
| Agriculture/cultivation | 5 events | 0 | 0% | Gradual; no dramatic culmination point |
| Defensive perimeter | 4 events | 0 | 0% | Preparatory rather than crisis-driven |
| **Subtotal** | **~37 events** | **0** | **0%** | |

**Pattern:** Civilizational advances are incremental, multi-turn, and "mundane" compared to relationship or arcane events. They are well-captured by the event extractor (37 events) but the original target page author focused on political/relationship arcs instead, treating material culture as background.

**Implication for synthesis:** The narrative generator must be explicitly prompted to include technological firsts. A "civilization milestones" input category should be included alongside events and relationships.

#### Category 2: Children and Family Events

| Subcategory | Events in catalog | In target v1 | Coverage | Why missed |
|---|---|---|---|---|
| Lyrawyn (birth, care, development) | 7 events | 0 (mentioned only in Kael's page) | 0% | Treated as a detail within Fenouille's story, not a named character |
| Faelan (birth, development) | 4 events | 0 (mentioned only in Kael's page) | 0% | Same |
| Rune (birth, conditions) | 4 events | 1 (birth) | 25% | Rune's arcane properties made her notable; siblings were "ordinary" |
| Fourth pregnancy | 3 events | 0 | 0% | Late-game; author may have missed or forgotten |
| Pregnancy discoveries | 4 events | 0 | 0% | Internal/personal events, not dramatic |
| **Subtotal** | **~22 events** | **1** | **5%** | |

**Pattern:** Children are well-represented in events but were treated as background details rather than narrative milestones. The target page author focused on Fenouille's *actions* and omitted her *personal life*. Only Rune's birth was included because it had arcane significance — the "ordinary" births of Lyrawyn and Faelan were invisible.

**Implication for synthesis:** Family events must be explicitly tagged as CRITICAL in the input. A synthesis prompt that says "include all births and pregnancy milestones" would prevent this category from being dropped.

#### Category 3: Survival Crises

| Subcategory | Events in catalog | In target v1 | Coverage | Why missed |
|---|---|---|---|---|
| Plague/sickness crisis | ~25 events (turns 225–252) | 0 | 0% | Enormous multi-turn arc with no single climactic event |
| Fragment/construct threat | ~30 events (turns 189–284) | ~2 brief mentions | ~7% | Same — diffuse, no single dramatic moment |
| **Subtotal** | **~55 events** | **~2** | **~4%** | |

**Pattern:** Multi-turn crisis arcs that span 30+ turns are the hardest category to compress. They have no single defining event — the plague crisis involves detection (turn 225), isolation (turns 232–233), dozens of experimental turns, resolution (turn 252), and codification (turns 253+). The target page author found it easier to skip entirely than to summarize.

**Implication for synthesis:** The phase segmentation algorithm (§3.3) must identify crisis arcs as coherent narrative units. A rule like "10+ consecutive events involving the same theme = a crisis arc worth summarizing" would catch these.

#### Category 4: Political Structure and Diplomacy

| Subcategory | Events in catalog | In target v1 | Coverage | Why missed |
|---|---|---|---|---|
| Council formation/meetings | 13 events | 1 (turn-108 mention) | 8% | Gradual; no founding moment |
| Southern tribes diplomacy | ~10 events | 1 brief mention | 10% | Scattered across turns 210–324 |
| Outsider integration (Thorne, Lyra, Gorok, Maelis) | ~25 events | 3 brief mentions | 12% | Late-game; complex multi-character interactions |
| Leadership delegation | 3 events | 1 (turn-301) | 33% | Single clear moment — easier to capture |
| **Subtotal** | **~51 events** | **~6** | **~12%** | |

**Pattern:** Political events are moderately captured. Single-moment milestones (delegation at turn-301) fare better than gradual processes (council formation). Diplomacy is especially poorly covered because it involves multiple external characters who are hard to track.

#### Category 5: Arcane Developments

| Subcategory | Events in catalog | In target v1 | Coverage | Why missed |
|---|---|---|---|---|
| Shaman connection | 5 events | 1 brief mention | 20% | Early-game; well-represented in events |
| Eastern expedition/construct | ~15 events | 1 brief mention | 7% | Multi-turn exploration arc |
| Disruption field invention | ~15 events | 1 mention (turn-286) | 7% | The invention process (turns 271–282) was entirely skipped |
| Ability gains | 3 events | 0 | 0% | Internal/mechanical events |
| **Subtotal** | **~38 events** | **~3** | **~8%** | |

#### Category 6: The Macro Civilization Arc

The overall transformation from *migrating hunter-gatherers under lean-tos* to *settled proto-village with governance* is the story's defining trajectory. It was **completely absent** from the original target page. No single event captures it — it emerges from the accumulation of longhouse + agriculture + pottery + education + governance + defensive infrastructure.

**Implication for synthesis:** The macro arc should be an explicit synthesis input — either as a manually curated "story arc" object (see §7.7) or as an emergent property that the narrative generator is prompted to identify. The refined target page (§1.2 v2) addresses this by structuring the biography around civilization-building phases.

#### Coverage Summary

| Category | Events in catalog | Covered in target v1 | Coverage % |
|---|---|---|---|
| Civilizational advances | ~37 | 0 | 0% |
| Children/family | ~22 | 1 | 5% |
| Survival crises | ~55 | ~2 | 4% |
| Political/diplomatic | ~51 | ~6 | 12% |
| Arcane developments | ~38 | ~3 | 8% |
| **Total unique** | **~200** | **~12** | **~6%** |

The event extractor captured the vast majority of significant events (they exist in the catalog). The information loss happens at the **synthesis/summarization stage** — the target page author selected only ~6% of available event data for the biography.

### 1.7 Personality and Desire Analysis

#### 1.7.1 Can personality be extracted from events alone?

**No.** Event descriptions capture *what happened* but not *how* or *why*. Compare:

| Source | Content | Personality signal |
|---|---|---|
| Event (evt-236) | "The player gives birth to a child with unique characteristics aligned with the principles of the Quiet Weave." | None — factual action |
| Transcript (turn-306 DM) | Fenouille's immediate response is clinical — she tests whether Rune is child or function, whether her presence alters the fragment or only resonates, whether she stabilizes or disrupts. | **Methodical empiricism** — even in emotional moments, she defaults to analysis |
| Event (evt-074) | "The player decides to build a durable, insulated communal longhouse for protection through the winter." | Weak — decision, but not *how* she decides |
| Transcript (turn-127 DM) | She etches blueprints into the snow and directs labor with cantrips and protective energies — a detailed architectural plan executed with magical precision. | **Strategic intelligence** + **civilization-building drive** — she doesn't just build, she blueprints |

**Conclusion:** Personality extraction requires reading the DM's narrative voice, not just action summaries. Events are necessary for *what* happened; transcript passages are necessary for *who she is*.

#### 1.7.2 Key personality evidence from transcript

**Fenouille Moonwind:**

| Trait | Turn | Transcript evidence |
|---|---|---|
| Strategic intelligence | turn-124 | Designs longhouse from blueprint with central hearth, alcoves, storage, cradle space — thinks in systems |
| Strategic intelligence | turn-210 | Issues 10 simultaneous directives covering agriculture, hunting, diplomacy, trapping, fragment study, and Anya's training |
| Charismatic leadership | turn-137 | Demonstrates cultivation concepts physically — seeds, clearing, planting — persuading through showing, not telling |
| Charismatic leadership | turn-301 | Tests whether the system survives without her by deliberately stepping back — confidence in delegation |
| Civilization-building drive | turn-136 | Launches full sedentarization program: planting, fishing, trapping, hunting improvements — each building on the last |
| Methodical empiricism | turn-238 | Tests plague by changing distance, resonance, direction, and environmental conditions — controlled experiments |
| Methodical empiricism | turn-271 | Tests disruption fields at outer boundary first, with minimal input, observing before escalating |
| Emotional depth | turn-114 | "I could pursue a stolen item which name I can't even remember, or grow a life with a hunter who loves me" — vulnerability |
| Controlled response to unknown | turn-307 | Immediately establishes conditions for Rune rather than celebrating — tests whether child is function or person |

**The Elder:**

| Trait | Turn | Transcript evidence |
|---|---|---|
| Reads character | turn-019 | Examines warlock attire and elven features, then assigns appropriate role — evaluates before acting |
| Communicates through symbol | turn-029 | Uses gestures, nods, dark material ritual — never direct speech in early game |
| Spiritual guardian | turn-340 | "I see patterns of what was and what forgets. Danger is we become deaf to the song of chaos" — philosophical warning |
| Preserves memory | turn-340 | Explicitly states his role: seeing what *was*, not what *can be made* — the archive, not the architect |

**Kael:**

| Trait | Turn | Transcript evidence |
|---|---|---|
| Stoic devotion | turn-051 | Acknowledges Fenouille with brief nod; warmth expressed through proximity, not words |
| Practical grounding | turn-344 | When asked what holds without patterns: "family bonds, old stories, clean water, shelter" — the essentials |
| Adaptive trust | turn-326 | Accepts Fenouille's discussion of strategic polygamy for Gorok despite personal discomfort — trust over jealousy |
| Growing independence | turn-340 | Reports analytical observations on predator patterns at edges — has become a thinker, not just a doer |

#### 1.7.3 Feasibility assessment

| Approach | Feasibility | Cost | Quality |
|---|---|---|---|
| Events only | Low — events capture actions, not character | 0 extra cost | Unreliable; infers personality from decisions only |
| Events + curated transcript excerpts | High — 3–5 key passages per character | ~500 tokens per character | Good; captures narrative voice and behavioral nuance |
| Full transcript | Very high but impractical | 2,000+ tokens per turn × 345 turns | Overkill; buries signal in noise |

**Recommendation:** Use a **curated key-moments approach**. For each character marked for personality extraction:
1. Identify 3–5 "personality-revealing turns" (can be tagged manually or by an LLM pre-pass)
2. Extract the relevant 200-word passage from the DM transcript for each
3. Pass these excerpts alongside events to the synthesis LLM
4. Prompt: "Based on these narrative passages, identify 3–5 personality traits with evidence"

This keeps cost manageable (~2,500 extra tokens per character) while providing the narrative texture that events alone cannot.

#### 1.7.4 Proposed personality schema extension

```json
{
  "personality": {
    "traits": [
      {
        "trait": "strategic intelligence",
        "evidence": "Designs longhouse from blueprint; issues 10 simultaneous directives covering agriculture, diplomacy, and arcane study",
        "source_turns": ["turn-124", "turn-210"],
        "confidence": 0.9,
        "source": "transcript"
      },
      {
        "trait": "methodical empiricism",
        "evidence": "Tests plague through controlled experiments; tests disruption fields at boundary before escalating",
        "source_turns": ["turn-238", "turn-271"],
        "confidence": 0.9,
        "source": "transcript"
      },
      {
        "trait": "civilization-building drive",
        "evidence": "Launches sedentarization program, introduces pottery/kiln, establishes governance structure",
        "source_turns": ["turn-136", "turn-177", "turn-291"],
        "confidence": 0.95,
        "source": "events+transcript"
      }
    ],
    "motivations": [
      {
        "goal": "Build a permanent, self-sufficient civilization",
        "evidence": "Introduces longhouse, agriculture, pottery, education, governance — each building on the last. Names settlement 'The Quiet Weave' with explicit philosophy.",
        "source_turns": ["turn-124", "turn-136", "turn-177", "turn-291"],
        "confidence": 0.95
      },
      {
        "goal": "Ensure generational continuity",
        "evidence": "Bears four children; establishes education system; delegates leadership to ensure survival beyond her",
        "source_turns": ["turn-141", "turn-183", "turn-306", "turn-312", "turn-301"],
        "confidence": 0.9
      }
    ],
    "interpersonal_style": "Leads through demonstration and delegation rather than command. Tests whether systems survive without her before trusting them."
  }
}
```

**Storage recommendation:** Store as a separate `{entity_id}.personality.json` sidecar file, not in the entity catalog. Reasons:
1. Personality data is derived (synthesis output), not extracted (catalog data)
2. Requires transcript access for high quality — different provenance than events
3. Can be regenerated independently of catalog updates
4. Keeps the entity catalog extraction-authoritative

---

## 2. Relationship Arc Design

### 2.1 The problem

`char-player` has 11 separate relationship history sections for `char-broad-figure` alone, containing entries like:
```
turn-012: communicating with
turn-035: working alongside
turn-051: helping maintain fire with
turn-056: friendship with
turn-078: laying a good luck kiss on his cheeks
turn-108: seeking to seal alliance with in the flesh
```

A human reader sees: "Stranger → work companion → friend → romantic partner → life partner." The raw data does not surface this arc.

### 2.2 Chunking strategy: hybrid type-transition + density

**Recommended: Relationship-type transitions as primary boundaries, with LLM refinement.**

1. **Phase 1 (rule-based)**: Group relationship history entries by their `type` field transitions. When the type changes (e.g., `social` → `romantic`), start a new chunk. Within the same type, cluster interactions that are within 20 turns of each other.

2. **Phase 2 (LLM-refined)**: Pass the rule-based chunks to the LLM with the instruction: "Given these interaction groups, name each phase and write a 1–2 sentence summary. Merge or split phases if the narrative warrants it."

Why not pure LLM? Rule-based chunking is deterministic and cheap— it handles the 80% case. The LLM pass adds narrative naming and catches cases where a type transition doesn't actually represent a meaningful arc shift.

Why not fixed turn windows? A 50-turn window would split the Fenouille–Kael romance across arbitrary boundaries. Relationship dynamics follow emotional beats, not clock time.

### 2.3 Arc summary format

#### JSON (stored in entity JSON, alongside raw history)

```json
{
  "target_id": "char-broad-figure",
  "arc_summary": [
    {
      "phase": "First Contact",
      "turn_range": ["turn-009", "turn-035"],
      "type": "social",
      "summary": "Initial encounters as strangers; communication through gesture and shared labor at the fire.",
      "key_turns": ["turn-012", "turn-035"]
    },
    {
      "phase": "Growing Closeness",
      "turn_range": ["turn-050", "turn-078"],
      "type": "social → romantic",
      "summary": "Friendship deepened through daily acts of care — shared meals, fire-tending, a good-luck kiss before the hunt.",
      "key_turns": ["turn-051", "turn-056", "turn-078"]
    },
    {
      "phase": "Partnership",
      "turn_range": ["turn-106", "turn-345"],
      "type": "romantic / leadership",
      "summary": "Became life partners and co-leaders of the tribe. Kael serves as acting leader and primary military advisor.",
      "key_turns": ["turn-108", "turn-194", "turn-210"]
    }
  ],
  "current_relationship": "Life partner and co-leader",
  "raw_history": [ /* ...existing history entries preserved... */ ]
}
```

#### Markdown (rendered in wiki page)

```markdown
### Kael (the young warrior / char-broad-figure)
**Arc: Stranger → Companion → Partner → Co-leader**

| Phase | Turns | Summary |
|---|---|---|
| First Contact | 009–035 | Communication through gesture and shared labor at the fire |
| Growing Closeness | 050–078 | Daily care deepened into open affection and a good-luck kiss |
| Partnership | 106–345 | Life partners and co-leaders; Kael as acting leader and military advisor |
```

### 2.4 Preservation policy

**Raw history is always preserved.** The `arc_summary` field is added alongside the existing `history` array, never replacing it. This ensures:
- Provenance is maintained (individual turn references survive)
- Arc summaries can be regenerated without data loss
- The raw data remains authoritative; arcs are derived/tagged content

### 2.5 Update strategy: extend-or-recompute

When a new interaction arrives:

1. **If the new interaction fits the latest arc's type and is within 30 turns of the last entry**: Append to the current arc. Regenerate only that arc's `summary` via LLM.
2. **If the new interaction represents a type transition or a gap > 30 turns**: Trigger a full re-summarization of all arcs for this relationship pair.
3. **Threshold**: Relationships with ≤ 3 total interactions skip arc summarization entirely — too little data to form meaningful arcs.

### 2.6 Handling duplicate relationship entries

The current data has multiple separate relationship objects for the same target (e.g., 11 entries for `char-broad-figure`). Before arc summarization:

1. Merge all relationship objects for the same `target_id` into a single unified history timeline.
2. Sort by turn number.
3. Deduplicate entries at the same turn.
4. Run arc summarization on the merged timeline.

This merge step is a prerequisite that also improves the base data quality.

---

## 3. Narrative Generation Design

### 3.1 Input assembly

The LLM receives a structured prompt with these sections, in priority order:

#### A. Events (primary — always available)

Filter `events.json` for entries where `related_entities` contains the target entity ID (or any known variant ID). Sort by `source_turns[0]`. This is the richest and most complete data source across all 345 turns.

For `char-player` (277 events), this is too large for a single prompt. Chunking strategy:
- **Biography generation**: Group events into narrative phases (see §3.3) and generate one phase at a time, each in a separate LLM call.
- **Maximum per-call**: ~40 events (~4,000 tokens of event descriptions). Enough for one narrative phase.
- **For entities with < 40 events** (most non-PC entities): Send all events in a single call.

#### B. Entity catalog data (supplementary — may be missing)

When a catalog entry exists, include:
- `identity` (1 sentence)
- `stable_attributes` (key-value pairs)
- `current_status` + `status_updated_turn`
- `volatile_state` (latest only)

When no catalog entry exists (Kael, Gorok, Lena, etc.): Skip this section. The prompt explicitly states "No catalog entry exists for this entity. Generate the biography from events only."

#### C. Relationship arc summaries (supplementary — when available)

Include summarized arcs (from §2) for the entity's most important relationships. Limit to top 5 relationships by interaction count.

#### D. Entity name resolution context

Provide a mapping of known ID variants: `"char-kael", "char-Kael" → Kael`. This prevents the LLM from treating ID variants as different characters.

### 3.2 Prompt structure

```
SYSTEM PROMPT:
You are a narrative biographer for an RPG campaign wiki. Your task is to
transform structured event data into readable prose.

Rules:
1. Use ONLY facts present in the provided data. Do not invent events,
   dialogue, motivations, or backstory not supported by the input.
2. Cite source turns inline using [turn-NNN] notation after each factual
   claim.
3. Write in third person past tense for biography sections.
4. Write in third person present tense for "Current Status" sections.
5. Organize biography by narrative phases, not by individual turns.
6. For relationships, describe the arc trajectory, not individual
   micro-interactions.
7. If uncertain about a connection (e.g., whether two entity IDs refer
   to the same person), note the uncertainty explicitly.
8. Keep each biography phase to 3–6 sentences.

USER PROMPT:
## Entity
Name: {name}
ID: {entity_id}
Type: {entity_type}
{identity_line}

## Known ID Variants
{id_variant_mapping}

## Catalog Data
{catalog_data_or_note_about_absence}

## Events (turns {start_turn}–{end_turn})
{event_list_formatted}

## Relationship Arcs
{arc_summaries}

## Task
Write the "{phase_name}" section of this entity's biography, covering
turns {start_turn} through {end_turn}. Cite turns inline.
```

### 3.3 Phase segmentation (pre-LLM step)

Before calling the LLM, segment the entity's event timeline into narrative phases. This is a rule-based step:

1. **PC (char-player)**: Use fixed phase boundaries based on event density and type transitions:
   - Scan for natural breakpoints: gaps of 10+ turns with no events, event-type shifts (e.g., several `encounter` events → several `decision` events)
   - Target 4–8 phases for a 345-turn character
   - Fallback: divide into equal-sized chunks of ~40 events each

2. **Major NPCs (10+ events)**: 2–4 phases, split at the largest event gaps.

3. **Minor NPCs (< 10 events)**: Single phase, no segmentation needed.

Each phase becomes one LLM call. A final LLM call generates the page summary/lede from the combined phase outputs.

### 3.4 Output format: markdown with structured metadata sidecar

The LLM produces **markdown prose** (the biography sections). A separate code step assembles the full page:

```
LLM output (per phase):  markdown prose with [turn-NNN] citations
Code assembly:            header + infobox + biography + relationships +
                          status + attributes + timeline appendix
Sidecar metadata:         {entity_id}.synthesis.json
```

#### Sidecar file: `{entity_id}.synthesis.json`

```json
{
  "entity_id": "char-player",
  "generated_at": "2026-04-15T12:00:00Z",
  "source_data": {
    "events_count": 277,
    "events_turn_range": ["turn-001", "turn-345"],
    "catalog_available": true,
    "catalog_last_updated": "turn-054",
    "relationship_arcs_count": 5
  },
  "phases": [
    {
      "name": "The Awakening",
      "turn_range": ["turn-001", "turn-014"],
      "events_used": ["evt-001", "evt-003", "evt-005", "evt-007", "evt-009"],
      "llm_model": "gpt-4o",
      "tokens_used": 1240
    }
  ],
  "provenance_check": {
    "turns_cited": ["turn-001", "turn-005", "turn-007", ...],
    "turns_available": ["turn-001", "turn-004", "turn-005", ...],
    "uncited_events": 12,
    "hallucination_flags": []
  }
}
```

This sidecar enables:
- Knowing when a page was generated and from what data
- Tracking LLM costs
- Post-generation provenance validation (§3.6)
- Incremental regeneration (only phases with new events)

### 3.5 Provenance tracking

**Inline citations** are the primary provenance mechanism: `[turn-NNN]` in the biography text.

Post-generation validation step (code, not LLM):
1. Extract all `[turn-NNN]` references from the generated markdown.
2. Compare against the events that were provided as input.
3. Flag any cited turn that was NOT in the input (potential hallucination).
4. Flag any major event from the input that was NOT cited (potential omission).
5. Write results to the sidecar `provenance_check` field.

If hallucination flags are found, the page is marked with a warning banner:
```markdown
> ⚠️ **Provenance warning**: This page cites turns not present in source data.
> Review flagged citations in char-player.synthesis.json.
```

### 3.6 Quality control: anti-hallucination measures

| Layer | Mechanism | Cost |
|---|---|---|
| **Prompt** | "Use ONLY facts present in the provided data" | Free |
| **Prompt** | Require `[turn-NNN]` citations for every claim | Free |
| **Post-gen** | Validate cited turns against input events | Code only |
| **Post-gen** | Check that major events appear in output | Code only |
| **Optional** | Second LLM pass: "Does this biography contain any claim not supported by the input?" | 1 extra LLM call |

The optional verification pass should be configurable — useful for high-stakes entities (PC, major NPCs) but too expensive for all 80+ entities.

### 3.7 When does it run?

**On-demand with incremental awareness.**

- `generate_wiki_pages.py --synthesize` triggers narrative generation.
- The sidecar tracks which events were included last time.
- On re-run, only phases with new events since last generation are regenerated.
- Full regeneration available via `--synthesize --force`.
- Without `--synthesize`, the tool produces the existing template-rendered pages (no LLM cost).

---

## 4. Events-First Architecture

### 4.1 Why events must be the primary source

Per the Phase 1 findings in [design-entity-pipeline-v3.md](design-entity-pipeline-v3.md):

| Data source | Coverage | Completeness |
|---|---|---|
| Events | 283 events, turns 1–345 | Covers all 345 turns with even distribution |
| Entity catalogs | 51 entities, turns 1–112 for characters | Major characters missing entirely |
| Relationships | Only between cataloged entities | Zero relationship data for Kael, Gorok, Lena, etc. |

The synthesis layer cannot depend on entity catalogs being complete. It must produce useful output from events alone and *enhance* that output when catalog data exists.

### 4.2 Entity resolution for events

Events reference entities by ad-hoc IDs that may not match catalog IDs. Before synthesis, a resolution step maps event entity IDs to canonical IDs:

```
Input:  event.related_entities = ["char-Kael", "char-tala", "faction-warrior-chief-gorok"]
Output: resolved = {"char-Kael": "char-kael", "char-tala": "char-tala",
                     "faction-warrior-chief-gorok": "char-gorok"}
```

This uses the same normalization logic recommended in [design-entity-pipeline-v3.md §5 Phase 1](design-entity-pipeline-v3.md). The synthesis layer consumes the normalized output — it does not implement its own ID resolution.

**If normalization has not yet been implemented** (#108 not yet fixed): The synthesis layer applies a lightweight fallback — case-insensitive ID matching and known-alias lookup from a manually curated mapping file.

### 4.3 Event-derived entity profiles

For entities that exist only in events (no catalog entry), the synthesis layer constructs a **derived entity profile** from event data:

```json
{
  "id": "char-kael",
  "name": "Kael",
  "type": "character",
  "source": "events_only",
  "first_event_turn": "turn-169",
  "last_event_turn": "turn-343",
  "event_count": 16,
  "co_occurring_entities": ["char-player", "char-tala", "char-lena"],
  "event_types": {"decision": 10, "recruitment": 2, "other": 4},
  "inferred_role": "Hunter/scout, acting leader, partner to Fenouille"
}
```

This profile is constructed by code (counting events, extracting co-occurrences) and passed to the LLM as context, tagged as `source: events_only` so the LLM knows not to assume catalog-level detail.

### 4.4 Graceful degradation by data availability

| Scenario | Biography quality | Relationships | Status |
|---|---|---|---|
| Events + catalog + relationships | Full narrative with detailed attributes | Arc summaries from relationship history | From catalog `current_status` |
| Events + catalog (no relationships) | Full narrative, relationship section from event co-occurrences | Inferred from shared events | From catalog |
| Events only (no catalog) | Narrative from event descriptions, attributes section sparse | Inferred from shared events | Derived from latest event description |
| Catalog only (no events) | Very limited — current template-style output | From catalog relationships | From catalog |

The design never fails to produce a page. It produces the best page possible with available data and clearly labels what data sources were used.

---

## 5. Pipeline Architecture

### 5.1 Component overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    SYNTHESIS PIPELINE                            │
│                                                                 │
│  ┌─────────────────────┐                                        │
│  │ 1. Data Assembly     │                                        │
│  │   • Load events.json │                                        │
│  │   • Load entity JSONs│                                        │
│  │   • Resolve IDs      │                                        │
│  │   • Group by entity  │                                        │
│  └──────────┬──────────┘                                        │
│             │                                                    │
│  ┌──────────▼──────────┐    ┌──────────────────────┐            │
│  │ 2. Relationship Arc  │    │ 3. Phase Segmentation │            │
│  │    Summarizer        │    │   (rule-based)        │            │
│  │   • Merge histories  │    │   • Event density     │            │
│  │   • Chunk by type    │    │   • Gap detection     │            │
│  │   • LLM: name + sum  │    │   • Type transitions  │            │
│  └──────────┬──────────┘    └──────────┬───────────┘            │
│             │                          │                         │
│             └──────────┬───────────────┘                         │
│                        │                                         │
│  ┌─────────────────────▼───────────────────────────────┐        │
│  │ 4. Narrative Biography Generator (LLM, per phase)    │        │
│  │   • System prompt with style/provenance rules        │        │
│  │   • Events + catalog + arcs as structured input      │        │
│  │   • Produces markdown prose with [turn-NNN] cites    │        │
│  └─────────────────────┬───────────────────────────────┘        │
│                        │                                         │
│  ┌─────────────────────▼───────────────────────────────┐        │
│  │ 5. Page Assembly (code, no LLM)                      │        │
│  │   • Infobox from catalog or event-derived profile    │        │
│  │   • Biography from LLM phases                        │        │
│  │   • Relationship arcs from step 2                    │        │
│  │   • Current status (catalog or latest event)         │        │
│  │   • Attributes table (catalog only)                  │        │
│  │   • Event timeline appendix                          │        │
│  └─────────────────────┬───────────────────────────────┘        │
│                        │                                         │
│  ┌─────────────────────▼───────────────────────────────┐        │
│  │ 6. Provenance Validation (code)                      │        │
│  │   • Extract cited turns from markdown                │        │
│  │   • Compare against source events                    │        │
│  │   • Flag hallucinations / omissions                  │        │
│  │   • Write sidecar .synthesis.json                    │        │
│  └─────────────────────────────────────────────────────┘        │
│                                                                 │
│  Output: {entity_id}.md + {entity_id}.synthesis.json            │
└─────────────────────────────────────────────────────────────────┘
```

### 5.2 Tool boundaries

| Component | Type | LLM calls | Location |
|---|---|---|---|
| Data assembly | Code | 0 | `tools/generate_wiki_pages.py` (extended) |
| Relationship arc summarizer | Code + LLM | 1 per relationship with 3+ interactions | New: `tools/synthesize_arcs.py` or integrated |
| Phase segmentation | Code | 0 | `tools/generate_wiki_pages.py` |
| Narrative biography generator | LLM | 1 per phase per entity | `tools/generate_wiki_pages.py` using `llm_client.py` |
| Page assembly | Code | 0 | `tools/generate_wiki_pages.py` |
| Provenance validation | Code | 0 (optionally 1 for verification) | `tools/generate_wiki_pages.py` |

**Recommendation: Single tool with internal stages, not multiple tools.**

Rationale: The stages have tight data dependencies (arcs feed biography, biography feeds page assembly). Splitting into separate CLI tools would require serializing/deserializing intermediate state. A single `generate_wiki_pages.py --synthesize` command that runs the full pipeline is simpler and ensures consistency.

The arc summarizer *could* be a separate tool (`synthesize_arcs.py`) if arc data is useful outside wiki generation (e.g., for `build_context.py`). Decision deferred to implementation.

### 5.3 File layout

```
framework-local/catalogs/characters/
  char-player.json              # Entity catalog (existing)
  char-player.md                # Synthesized wiki page (replaces current)
  char-player.synthesis.json    # Generation metadata sidecar (new)
```

The `.md` file replaces the current template-rendered page when `--synthesize` is used. Without `--synthesize`, the existing template renderer produces the current format (backward compatible).

### 5.4 Interaction with `build_context.py`

`build_context.py` produces `turn-context.json` for the analysis agent. Currently it reads entity catalog JSON only. Two integration points:

1. **Arc summaries in context**: If relationship arcs are stored in entity JSON (§2.3), `build_context.py` automatically picks them up when loading entity data. No changes needed.

2. **Event-derived profiles for missing entities**: `build_context.py` could optionally load event-derived profiles (§4.3) for entities mentioned in events but missing from catalogs. This would improve the analysis agent's context for late-game turns. However, this is a separate enhancement — the synthesis layer produces the profiles, `build_context.py` decides whether to consume them.

### 5.5 LLM cost estimate

| Entity type | Count | Avg events | Phases | LLM calls (bio) | LLM calls (arcs) |
|---|---|---|---|---|---|
| PC (char-player) | 1 | 277 | 6 | 7 (6 phases + 1 lede) | ~8 (top relationships) |
| Major NPCs (10+ events) | ~8 | 15 | 2 | 24 (8 × 3) | ~15 |
| Minor NPCs (3–9 events) | ~15 | 5 | 1 | 15 | ~5 |
| Locations | ~12 | 3 | 1 | 12 | 0 |
| Factions | ~5 | 2 | 1 | 5 | 0 |
| Items | ~18 | 1 | 0 | 5 (only notable items) | 0 |
| **Total** | | | | **~68** | **~28** |

At ~1,500 tokens per call average, total cost: ~144K tokens ≈ $0.45 with GPT-4o, or free with local Ollama. Full regeneration from scratch; incremental runs much cheaper.

---

## 6. Entity Type Adaptations

### 6.1 Priority order

| Type | Narrative value | Current data quality | Priority |
|---|---|---|---|
| **Characters** | Very high — the story IS the characters | 51 catalog + 34 orphan IDs | 1st |
| **Locations** | Medium — locations define setting but evolve slowly | 12 catalog, few events directly | 3rd |
| **Factions** | Medium — political dynamics are important | 4 catalog, mostly early-game | 4th |
| **Items** | Low–medium — a few key items (fragment, brew, herbs) are plot-relevant | 18 catalog, few events | 2nd (for key items only) |

### 6.2 Character pages (primary design — see §§1–5)

Full narrative biography with relationship arcs, event timeline, and provenance.

### 6.3 Location pages

Locations benefit from a **chronological event log** more than narrative prose.

```markdown
# The Encampment

> The main tribal settlement, later known as the Quiet Weave.

## Significance

The encampment serves as the central location for the tribal community.
Initially a rough camp of crude lean-tos and a central bonfire, it evolved
into a structured settlement with longhouses, cultivation plots, disruption
fields, and dedicated crafting areas.

## Key Events at This Location

| Turn | Event |
|---|---|
| turn-015 | Fenouille brought here as captive by two figures |
| turn-108 | Council meeting: agriculture, fragment study, alliances |
| turn-286 | Triangular disruption fields established |
| turn-306 | Birth of Rune in the longhouse |

## Connected Entities

| Entity | Connection |
|---|---|
| char-player | Resident, leader |
| char-elder | Overseer |
| char-broad-figure | Warrior/guardian |
```

**Adaptation**: Replace the biography section with a "Significance" summary (1 LLM call) + chronological event table (code). No relationship arcs needed.

### 6.4 Faction pages

Factions benefit from **membership evolution** and **goal tracking**.

```markdown
# The Southern Tribes

## History

Initially encountered as a potential threat — warnings about competition for
resources [turn-108]. Fenouille initiated diplomacy through shared preservation
techniques and proposed hunting boundaries. By turn-340, Thorne's people had
successfully applied disruption field methods and requested permanent settlers
for deeper training.

## Known Members

| Member | Role | First Seen |
|---|---|---|
| Thorne | Leader | turn-340 |

## Current Relations

Allied — requesting deeper integration with the Quiet Weave's methods.
```

**Adaptation**: Biography becomes a "History" section focused on the faction's stance and trajectory. Member list derived from events where faction members are mentioned. 1 LLM call for history synthesis.

### 6.5 Item pages

Most items don't need narrative synthesis. Only **plot-significant items** benefit:

```markdown
# The Arcane Fragment

## Significance

A mysterious object of profound arcane order, the fragment serves as the
anchoring force of the Quiet Weave. It emits a resonant thrum that intensified
over time, producing geometric frost crystals and influencing the land's
patterns [turn-108, turn-306].

## Key Events

| Turn | Event |
|---|---|
| turn-108 | Meticulous measurement begins — freezing patterns, ice timing |
| turn-306 | Flares with blue-white light at Rune's birth |
| turn-340 | Deepened thrum, anchoring hum resonating with the fields |
```

**Adaptation**: Only items referenced in 3+ events get narrative synthesis. Others keep the template-rendered format. 1 LLM call per notable item.

### 6.6 Entity type detection for synthesis

```python
def should_synthesize(entity_id: str, event_count: int, entity_type: str) -> bool:
    """Determine whether an entity warrants narrative synthesis."""
    if entity_type == "character":
        return event_count >= 3
    if entity_type in ("location", "faction"):
        return event_count >= 3
    if entity_type == "item":
        return event_count >= 3
    return False
```

Entities below the threshold get the existing template-rendered page.

---

## 7. Open Questions

### 7.1 Transcript access in synthesis prompts

**Question**: Should the LLM receive raw transcript excerpts for key turns, or only structured event data?

**Trade-offs**:
- **Events only**: Cheaper, consistent, no risk of leaking raw transcript content. But event descriptions are compressed (1 sentence per event) and lose narrative voice.
- **Transcript excerpts**: Richer prose, captures the DM's narrative style. But much more expensive (some turns are 2,000+ tokens), risks hallucination from tangential details, and the repo rule says raw transcripts are immutable sources — passing them to an LLM for synthesis feels closer to "modifying" than "deriving from."

**Recommendation**: Start with events only. If biography quality is too dry, add transcript excerpts for a curated subset of "key turns" (e.g., turns with `birth`, `arrival`, `death` events).

### 7.2 Should `char-broad-figure` be merged with `char-kael`?

**Question**: The design document for V3 notes this is likely the same person. Should the synthesis layer handle this merge, or should it wait for #108 fixes?

**Recommendation**: The synthesis layer should not merge entities. It should note the suspected connection in the generated page (as shown in the Kael example, §1.3) but leave authoritative merging to the extraction pipeline (#108). The synthesis layer consumes resolved IDs; it does not create them.

### 7.3 Arc summary storage location

**Question**: Should relationship arc summaries live in the entity JSON (modifying the catalog), or in a separate synthesis output file?

**Options**:
- **In entity JSON**: Arc summaries are immediately available to `build_context.py` and other tools. But modifies the catalog, which should be extraction-authoritative.
- **Separate file**: Cleaner separation of concerns. But requires tools to look in two places.

**Recommendation**: Store in a separate `{entity_id}.arcs.json` file alongside the entity JSON. The catalog remains extraction-only; synthesis outputs are clearly derived.

### 7.4 Tone and style

**Question**: What narrative voice should biographies use?

**Options**:
- **Encyclopedic** (Wikipedia-style): "Fenouille Moonwind is an elven warlock who..."
- **Campaign chronicle**: "In the dead of winter, an elf awoke face-down in the snow..."
- **Player-focused reference**: "Your character awakened in the snow and..."

**Recommendation**: Encyclopedic third-person past tense for biography sections, present tense for current status. This is a reference wiki, not fiction. The DM's narrative voice lives in the transcript; the wiki summarizes it.

### 7.5 What is the minimum useful LLM for synthesis?

The local model (qwen2.5:14b) struggles with complex extraction tasks. Can it handle narrative synthesis?

Synthesis prompts are simpler than extraction: "Given these events, write a 3–6 sentence paragraph." No JSON parsing, no schema compliance. The 14B model should handle this adequately. Testing recommended before committing.

### 7.6 Personality extraction feasibility

**Question:** Can the local 14B model extract personality traits, or does this require a stronger model?

Personality extraction (§1.7) has two distinct sub-tasks:

1. **Identifying personality-revealing moments** from a list of events: This requires judgment about which events carry character information vs. plot information. The 14B model can likely handle this with explicit prompt guidance ("Which of these events reveal something about the character's personality, decision-making style, or motivations?").

2. **Synthesizing trait descriptions from narrative passages**: This requires reading 200-word DM passages and articulating *what they reveal about character*. This is closer to literary analysis and may exceed the 14B model's capabilities for subtle traits.

**Recommendation:** Test with a two-tier approach:
- **Tier 1 (14B-capable):** Extract traits from *actions* in event descriptions — "makes strategic plans," "delegates authority," "tests before deploying." These are observable behaviors.
- **Tier 2 (requires stronger model):** Extract traits from *narrative voice* in transcript passages — emotional responses, interpersonal style, implicit motivations. Reserve for GPT-4o or equivalent.

For the initial implementation, personality can be derived from Tier 1 alone. Tier 2 is a quality enhancement that can be added when stronger models are available locally.

### 7.7 Civilization arc as structural element

**Question:** Should the macro arc (hunter-gatherers → settled village) be an explicit synthesis input (a "story arc" object), or should it emerge from event sequencing?

The §1.6 information loss taxonomy shows that the macro civilization arc was completely absent from the original target page despite being the story's defining trajectory. This suggests it will **not** emerge naturally from event sequencing — the synthesis LLM will face the same summarization pressure that caused the human author to omit it.

**Options:**

1. **Explicit "story arc" input:** A manually curated JSON object describing the macro arc with phase markers. The synthesis prompt references it: "This character's story follows a civilization-building arc from X to Y. Ensure the biography reflects this progression."

   ```json
   {
     "arc_id": "civilization-building",
     "description": "Migrating hunter-gatherers → settled proto-village with governance",
     "phases": [
       {"name": "Primitive camp", "turn_range": ["turn-001", "turn-120"], "marker": "Crude lean-tos, bonfire, no permanent structures"},
       {"name": "Longhouse revolution", "turn_range": ["turn-121", "turn-134"], "marker": "First permanent structure"},
       {"name": "Sedentarization", "turn_range": ["turn-135", "turn-183"], "marker": "Agriculture, pottery, food preservation"},
       {"name": "Crisis and expansion", "turn_range": ["turn-184", "turn-290"], "marker": "Plague, fragment, disruption fields"},
       {"name": "Formalized settlement", "turn_range": ["turn-291", "turn-344"], "marker": "Named 'Quiet Weave', governance, diplomacy"}
     ],
     "key_entities": ["char-player"],
     "source": "manual_curation"
   }
   ```

2. **Emergent from event tags:** Tag events with a `civilizational_significance` field during extraction or post-processing. The synthesis pipeline groups these and lets the LLM infer the arc.

3. **Prompt-only:** Add a synthesis prompt instruction: "Look for macro-level transformation arcs across the full event timeline. If the entity's events show a progression from one state to another (e.g., nomadic → settled), make this the organizing principle of the biography."

**Recommendation:** Option 1 for the PC (most important, curated quality), Option 3 for NPCs (let the LLM try). Story arc objects could live in `framework/story/arcs.json` alongside the existing `summary.md`. This is a low-cost enhancement with high narrative impact.

The curated arc approach also helps with the §1.6 finding that civilizational advances have 0% coverage in target pages — by making the arc an explicit input, we guarantee the synthesis pipeline won't drop it.

---

## 8. Implementation Plan

### 8.1 What can be built NOW (current data, no pipeline fixes)

| Step | Description | Dependencies | LLM needed |
|---|---|---|---|
| 1 | **Event-entity grouping**: Code to group events by entity ID with case-insensitive matching | None | No |
| 2 | **Event-derived profiles**: Code to build profiles for event-only entities (§4.3) | Step 1 | No |
| 3 | **Phase segmentation**: Code to divide event timelines into narrative phases (§3.3) | Step 1 | No |
| 4 | **Relationship history merger**: Code to merge duplicate relationship entries (§2.6) | None | No |
| 5 | **Relationship arc summarizer**: LLM-assisted arc naming and summarization (§2.2) | Step 4 | Yes |
| 6 | **Narrative biography generator**: Core LLM pipeline (§3.1–3.4) | Steps 1–3, 5 | Yes |
| 7 | **Page assembly**: Code to combine LLM output + data into final markdown (§5.1) | Step 6 | No |
| 8 | **Provenance validation**: Post-gen turn citation checking (§3.5) | Step 7 | No |
| 9 | **Sidecar generation**: Write `.synthesis.json` metadata (§3.4) | Step 7 | No |

Steps 1–4 are pure code, testable immediately with current data. Steps 5–6 require LLM access but work with current incomplete data because they draw primarily from events.

### 8.2 What improves after #106/#107/#108 fixes

| Pipeline fix | Synthesis benefit |
|---|---|
| #108 (ID normalization) | Event-entity grouping becomes exact-match instead of fuzzy. Kael events consolidate under one ID. |
| #106 (entity discovery gap) | Kael, Gorok, Lena etc. get catalog entries → richer biographies with attributes and relationships |
| #107 (PC detail stall) | `char-player` catalog reflects turn-345 → better current status, complete attributes |

The synthesis layer is designed to produce useful output before these fixes AND improve automatically when they land. No synthesis code changes needed — only the input data quality improves.

### 8.3 Suggested implementation order

1. **Steps 1–4** (code only): Establish the data assembly layer. Write tests using current Run 4 data. Validate that event grouping, profile generation, phase segmentation, and relationship merging produce correct intermediate data.

2. **Step 5** (arcs): Implement arc summarization. Test with `char-player ↔ char-broad-figure` relationship (richest history data). Evaluate LLM output quality with both GPT-4o and local qwen2.5:14b.

3. **Steps 6–9** (narrative + assembly): Implement the full biography pipeline. Start with Kael (event-only) and char-player (full data) as test cases. Validate provenance checking catches real issues.

4. **Entity type variants** (§6): Extend to locations, factions, items. Lower priority — characters are the primary value.

### 8.4 Testing strategy

- **Unit tests**: Event grouping, phase segmentation, relationship merging, provenance extraction (all deterministic code).
- **Snapshot tests**: Store expected LLM output for char-player and char-kael at a fixed prompt. Compare on changes to detect prompt regression.
- **Provenance integration test**: Generate a page, extract citations, verify all cited turns exist in source events.
- **Manual review**: First generated pages for char-player and char-kael should be manually reviewed against the target examples in §1.2 and §1.3.
