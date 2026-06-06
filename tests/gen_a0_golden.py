"""Regenerate the Phase A0 flag-OFF byte-identity golden.

Epic #477, issue #482 (PR #483).  The golden at
``tests/golden/checkpoint_compaction/flag_off_prior.json`` pins the flag-OFF
prior-state render to byte-identity with ``main`` (the A/B control for
``TestFlagOffGolden::test_flag_off_matches_main_golden``).

Regenerate it by running this script FROM ``main`` (or any branch where the
flag-OFF ``_format_prior_entity_context`` path is unchanged), so the frozen
literal always reflects the unmodified baseline rather than the A0 branch::

    python tests/gen_a0_golden.py

The entity builder below MUST stay identical to ``_vol_entry`` in
``tests/test_checkpoint_compaction.py`` so the test and the golden agree.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import semantic_extraction as se  # noqa: E402

_GOLDEN_PATH = os.path.join(
    os.path.dirname(__file__),
    "golden",
    "checkpoint_compaction",
    "flag_off_prior.json",
)


def _vol_entry(num_turns=100):
    """Deterministic PC entry with a long-running volatile block.

    Must match ``_vol_entry`` in tests/test_checkpoint_compaction.py.
    """
    return {
        "id": "char-player",
        "name": "Player Character",
        "type": "character",
        "first_seen_turn": "turn-001",
        "last_updated_turn": f"turn-{num_turns:03d}",
        "identity": "The protagonist",
        "current_status": "active",
        "stable_attributes": {
            "species": "human",
            "class": "ranger",
            "aliases": {"value": ["Hero"]},
        },
        "volatile_state": {
            "location": [
                {"turn": f"turn-{i:03d}", "value": f"place-{i}"}
                for i in range(1, num_turns + 1)
            ],
            "mood": [
                {"turn": f"turn-{i:03d}", "value": f"feeling-{i}"}
                for i in range(10, num_turns + 1, 10)
            ],
        },
    }


def main():
    out = se._format_prior_entity_context(
        _vol_entry(100), config=None, mentioned_ids=set(), current_turn_num=100
    )
    os.makedirs(os.path.dirname(_GOLDEN_PATH), exist_ok=True)
    with open(_GOLDEN_PATH, "w", encoding="utf-8", newline="") as fh:
        fh.write(out)
    print(f"Wrote {len(out)} chars to {_GOLDEN_PATH}")


if __name__ == "__main__":
    main()
