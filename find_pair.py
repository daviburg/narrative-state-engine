"""Find test pair: substring but not token prefix, lev > 3, ratio < 0.6."""
from difflib import SequenceMatcher
import sys, os
sys.path.insert(0, 'tools')
from semantic_extraction import _levenshtein

pairs = [
    ('oaks', 'cloaks of shadow'),
    ('ember', 'remembrance'),
    ('arch', 'parchment scroll'),
    ('iron', 'environment'),
    ('lore', 'explorer guild'),
    ('vine', 'wolverine pack'),
]
for a, b in pairs:
    d = _levenshtein(a, b)
    r = SequenceMatcher(None, a, b).ratio()
    sub = a in b
    tp = any(t.startswith(a) for t in b.replace('-', ' ').split())
    print(f'lev={d} ratio={r:.3f} sub={sub} tp={tp}  {a!r} in {b!r}')
