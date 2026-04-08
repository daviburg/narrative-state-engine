# Plot Thread Sheet Template

Use as reference for `framework/catalogs/plot-threads.json` entries.
Conforms to `schemas/plot-thread.schema.json`.

---

## Fields

| Field | Required | Notes |
|---|---|---|
| `id` | Yes | Stable identifier, e.g. `thread-missing-scholar` |
| `title` | Yes | Short descriptive title |
| `description` | Yes | What this thread is about; factual summary only |
| `status` | Yes | `active`, `resolved`, `suspended`, `unknown` |
| `stakes` | No | What is at risk if this thread goes badly |
| `related_entities` | No | Entity IDs relevant to this thread |
| `open_questions` | No | List of unresolved questions the thread raises |
| `likely_paths` | No | Possible directions; tag as inference |
| `first_seen_turn` | Yes | Turn when this thread emerged |
| `last_updated_turn` | Yes | Most recent turn that affected this thread |
| `source_refs` | Yes | Supporting turn IDs |

---

## Example entry

```json
{
  "id": "thread-missing-scholar",
  "title": "Missing scholar investigation",
  "description": "The player is investigating a colleague who visited Thornhaven two months ago to study local ruins and has not returned. The innkeeper reacted evasively when asked.",
  "status": "active",
  "stakes": "Unknown; colleague may be in danger or may have discovered something significant about the ruins.",
  "related_entities": ["char-innkeeper-thornhaven", "loc-thornhaven", "loc-old-ruins"],
  "open_questions": [
    "What happened to the scholar?",
    "What are the ruins and why were they being studied?",
    "Why did the innkeeper react evasively?"
  ],
  "likely_paths": [
    "(inference, 0.6) Scholar entered the ruins and has not emerged",
    "(inference, 0.4) Locals know what happened but are protecting someone or something"
  ],
  "first_seen_turn": "turn-005",
  "last_updated_turn": "turn-006",
  "source_refs": ["turn-005", "turn-006"]
}
```
