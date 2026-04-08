# Location Sheet Template

Use as reference for `framework/catalogs/locations.json` entries.
Conforms to `schemas/entity.schema.json` with `entity_type: location`.

---

## Fields

| Field | Required | Notes |
|---|---|---|
| `id` | Yes | Stable identifier, e.g. `loc-thornhaven` |
| `name` | Yes | Canonical name as stated by DM |
| `aliases` | No | Alternative names or descriptions used in the transcript |
| `entity_type` | Yes | Always `location` |
| `description` | Yes | Factual summary from transcript only |
| `current_status` | Yes | e.g. `accessible`, `locked`, `unknown`, `dangerous` |
| `attributes` | No | Notable features; tag inferences |
| `relationships` | No | Connected entity IDs (NPCs present, parent locations, etc.) |
| `first_seen_turn` | Yes | Turn when this location first appeared |
| `last_updated_turn` | Yes | Most recent turn that updated this entry |
| `confidence` | Yes | 0.0–1.0 |
| `source_refs` | Yes | Supporting turn IDs |

---

## Example entry

```json
{
  "id": "loc-the-broken-wheel",
  "name": "The Broken Wheel",
  "aliases": ["the inn", "the only lit building"],
  "entity_type": "location",
  "description": "The only inn in Thornhaven. Warm; smells of old smoke. Three villagers at a corner table. Bar staffed by the unnamed innkeeper. Only building showing light after dusk.",
  "current_status": "accessible",
  "attributes": {
    "parent_location": "loc-thornhaven",
    "atmosphere": "guarded, locals unwilling to engage with strangers",
    "rooms_available": true
  },
  "relationships": {
    "char-innkeeper-thornhaven": "proprietor present",
    "loc-thornhaven": "located within"
  },
  "first_seen_turn": "turn-002",
  "last_updated_turn": "turn-006",
  "confidence": 1.0,
  "source_refs": ["turn-002", "turn-004"]
}
```
