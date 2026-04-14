# Character Sheet Template

Use this as a reference for catalog entries in `framework/catalogs/characters/`.
Each character entry should conform to `schemas/entity.schema.json` (V2 format).

> **Note:** Human-readable wiki-style markdown pages are generated automatically
> by `tools/generate_wiki_pages.py` alongside each per-entity JSON file.
> Do not edit `.md` files in catalog directories manually.

---

## Fields (V2 Schema)

| Field | Required | Notes |
|---|---|---|
| `id` | Yes | Stable identifier, e.g. `char-innkeeper-thornhaven`. Never change after first use. |
| `name` | Yes | Canonical name or best-known name |
| `type` | Yes | Always `character` for this template |
| `identity` | Yes | Factual one-sentence summary based on explicit transcript evidence |
| `current_status` | Yes | Prose description of what the character is currently doing |
| `status_updated_turn` | No | Turn ID when `current_status` was last changed |
| `stable_attributes` | No | Dict of attribute objects, each with `value`, `inference`, `confidence`, `source_turn` |
| `volatile_state` | No | Dict with `condition`, `equipment`, `location`, `last_updated_turn` |
| `relationships` | No | Array of relationship objects with `target_id`, `current_relationship`, `type`, `status`, `history` |
| `first_seen_turn` | Yes | Turn ID when this character first appeared |
| `last_updated_turn` | Yes | Most recent turn that changed this entry |

---

## Example entry (V2)

```json
{
  "id": "char-innkeeper-thornhaven",
  "name": "Unknown innkeeper",
  "type": "character",
  "identity": "A heavyset woman with grey-streaked hair running The Broken Wheel inn. Watchful and guarded.",
  "current_status": "Serving drinks behind the bar, keeping a wary eye on newcomers.",
  "status_updated_turn": "turn-006",
  "stable_attributes": {
    "aliases": {
      "value": ["the woman at the bar", "heavy-set woman"],
      "inference": false,
      "confidence": 1.0,
      "source_turn": "turn-002"
    },
    "occupation": {
      "value": "innkeeper",
      "inference": false,
      "confidence": 1.0,
      "source_turn": "turn-002"
    },
    "disposition_toward_player": {
      "value": "neutral-guarded",
      "inference": true,
      "confidence": 0.7,
      "source_turn": "turn-004"
    }
  },
  "volatile_state": {
    "condition": "alert",
    "equipment": ["bar towel"],
    "location": "The Broken Wheel, Thornhaven",
    "last_updated_turn": "turn-006"
  },
  "relationships": [
    {
      "target_id": "loc-the-broken-wheel",
      "current_relationship": "proprietor",
      "type": "professional",
      "status": "active",
      "first_seen_turn": "turn-002",
      "last_updated_turn": "turn-002"
    }
  ],
  "first_seen_turn": "turn-002",
  "last_updated_turn": "turn-006"
}
```

---

## Rules
- Do not add attributes that have not appeared in the transcript.
- Tag any inferred attribute with `(inferred, confidence X.X)`.
- Never rename an entity's `id` after it has been used in other files.
