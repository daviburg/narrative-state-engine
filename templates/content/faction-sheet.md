# Faction Sheet Template

Use as reference for `framework/catalogs/factions.json` entries.
Conforms to `schemas/entity.schema.json` with `entity_type: faction`.

---

## Fields

| Field | Required | Notes |
|---|---|---|
| `id` | Yes | Stable identifier, e.g. `faction-thornhaven-locals` |
| `name` | Yes | Canonical faction name |
| `aliases` | No | Other names or descriptions used in the transcript |
| `entity_type` | Yes | Always `faction` |
| `description` | Yes | Factual summary; explicit evidence only |
| `current_status` | Yes | e.g. `active`, `disbanded`, `unknown` |
| `attributes` | No | Key traits; tag inferences |
| `relationships` | No | Related entity IDs and relationship type |
| `first_seen_turn` | Yes | Turn when this faction was first identified |
| `last_updated_turn` | Yes | Most recent turn affecting this entry |
| `confidence` | Yes | 0.0–1.0 |
| `source_refs` | Yes | Supporting turn IDs |

---

## Key attributes to track

- `disposition_toward_player`: current attitude (neutral, hostile, allied, unknown)
- `known_goals`: what the faction appears to want (tag inferences)
- `known_members`: list of character IDs who are confirmed or suspected members
- `power_base`: what resources or leverage the faction controls

---

## Example entry

```json
{
  "id": "faction-thornhaven-locals",
  "name": "Thornhaven villagers",
  "aliases": ["the locals", "Thornhaven residents"],
  "entity_type": "faction",
  "description": "The residents of Thornhaven. Collectively guarded and unwilling to discuss recent events with outsiders. Cover for each other when questioned.",
  "current_status": "active",
  "attributes": {
    "disposition_toward_player": "neutral-guarded",
    "known_goals": "(inferred, 0.5) Protecting something or someone connected to the old ruins",
    "known_members": ["char-innkeeper-thornhaven", "char-young-man-bandaged-arm"],
    "power_base": "local information advantage; control of the only lodging"
  },
  "relationships": {
    "loc-thornhaven": "resident population",
    "loc-old-ruins": "(inferred) connection unknown"
  },
  "first_seen_turn": "turn-002",
  "last_updated_turn": "turn-006",
  "confidence": 0.8,
  "source_refs": ["turn-002", "turn-004", "turn-006"]
}
```
