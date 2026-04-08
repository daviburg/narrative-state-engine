# Character Sheet Template

Use this as a reference for catalog entries in `framework/catalogs/characters.json`.
Each character entry should conform to `schemas/entity.schema.json`.

---

## Fields

| Field | Required | Notes |
|---|---|---|
| `id` | Yes | Stable identifier, e.g. `char-innkeeper-thornhaven`. Never change after first use. |
| `name` | Yes | Canonical name or best-known name |
| `aliases` | No | Other names or titles observed in the transcript |
| `entity_type` | Yes | Always `character` for this template |
| `description` | Yes | Factual summary from explicit transcript evidence only |
| `current_status` | Yes | e.g. `active`, `deceased`, `unknown`, `ally`, `hostile` |
| `attributes` | No | Key/value pairs for notable traits. Tag inferences with `(inferred)` |
| `relationships` | No | Map of related entity IDs to relationship description |
| `first_seen_turn` | Yes | Turn ID when this character first appeared |
| `last_updated_turn` | Yes | Most recent turn that changed this entry |
| `confidence` | Yes | 0.0–1.0; use 1.0 only for directly stated facts |
| `source_refs` | Yes | List of turn IDs that support this entry |

---

## Example entry

```json
{
  "id": "char-innkeeper-thornhaven",
  "name": "Unknown innkeeper",
  "aliases": ["the woman at the bar", "heavy-set woman"],
  "entity_type": "character",
  "description": "A heavyset woman with grey-streaked hair. Runs The Broken Wheel inn in Thornhaven. Watchful and guarded; did not offer her name.",
  "current_status": "active",
  "attributes": {
    "occupation": "innkeeper",
    "location": "The Broken Wheel, Thornhaven",
    "disposition_toward_player": "neutral-guarded",
    "hiding_something": "(inferred, confidence 0.7) Grip tightened when scholar was mentioned"
  },
  "relationships": {
    "loc-the-broken-wheel": "proprietor",
    "loc-thornhaven": "resident"
  },
  "first_seen_turn": "turn-002",
  "last_updated_turn": "turn-006",
  "confidence": 0.9,
  "source_refs": ["turn-002", "turn-004", "turn-006"]
}
```

---

## Rules
- Do not add attributes that have not appeared in the transcript.
- Tag any inferred attribute with `(inferred, confidence X.X)`.
- Never rename an entity's `id` after it has been used in other files.
