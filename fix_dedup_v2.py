"""Replace _within_turn_dedup function with fixed version.

Uses line-based replacement to avoid regex issues.
"""
lines = open('tools/semantic_extraction.py', 'r', encoding='utf-8').readlines()

# Find the function start
start = None
for i, line in enumerate(lines):
    if line.strip() == 'def _within_turn_dedup(entities: list[dict]) -> list[dict]:':
        start = i
        break

if start is None:
    print("ERROR: _within_turn_dedup function not found")
    exit(1)

print(f"Found function at line {start + 1}")

# Find the function end: next line at module level (not indented, not blank)
end = None
for i in range(start + 1, len(lines)):
    line = lines[i]
    if line.strip() and not line[0].isspace():
        end = i
        break

if end is None:
    print("ERROR: Could not find end of function")
    exit(1)

print(f"Function spans lines {start + 1}-{end}")
print(f"Old function:\n{''.join(lines[start:end])}")

new_func = '''def _within_turn_dedup(entities: list[dict]) -> list[dict]:
    """Deduplicate is_new entities discovered in the same turn (#365).

    For each pair of is_new=True entities, checks:
    - Character substring with token-prefix guard: the shorter name (>=4 chars)
      must be a prefix of a token in the longer name (prevents false merges like
      fire/fireplace)
    - Levenshtein distance <= 3
    - SequenceMatcher ratio >= 0.6

    Keeps the entity with the longer name (more specific).
    Preserves original entity ordering.
    """
    from difflib import SequenceMatcher

    # Build index of which positions in *entities* are is_new
    new_indices = [i for i, e in enumerate(entities) if e.get("is_new", False)]

    if len(new_indices) < 2:
        return entities

    # Track which original indices to drop
    drop_indices: set[int] = set()

    for ni in range(len(new_indices)):
        idx_a = new_indices[ni]
        if idx_a in drop_indices:
            continue
        name_a = entities[idx_a].get("name", "").strip().lower()
        if not name_a:
            continue
        for nj in range(ni + 1, len(new_indices)):
            idx_b = new_indices[nj]
            if idx_b in drop_indices:
                continue
            name_b = entities[idx_b].get("name", "").strip().lower()
            if not name_b:
                continue

            matched = False

            # Check 1: Character substring with token-prefix guard (both >= 4 chars)
            if len(name_a) >= 4 and len(name_b) >= 4:
                if name_a in name_b or name_b in name_a:
                    shorter = name_a if len(name_a) <= len(name_b) else name_b
                    longer = name_b if len(name_a) <= len(name_b) else name_a
                    longer_tokens = longer.replace("-", " ").split()
                    # Token-prefix guard: shorter must be a prefix of at least
                    # one token in the longer name (matches _dedup_catalogs 2b)
                    if any(t.startswith(shorter) for t in longer_tokens):
                        matched = True

            # Check 2: Levenshtein distance <= 3
            if not matched:
                dist = _levenshtein(name_a, name_b)
                if dist <= 3:
                    matched = True

            # Check 3: SequenceMatcher ratio >= 0.6
            if not matched:
                ratio = SequenceMatcher(None, name_a, name_b).ratio()
                if ratio >= 0.6:
                    matched = True

            if matched:
                # Drop the shorter name, keep the longer (more specific)
                if len(name_a) >= len(name_b):
                    drop_idx = idx_b
                    keep_name = name_a
                    drop_name = name_b
                else:
                    drop_idx = idx_a
                    keep_name = name_b
                    drop_name = name_a
                drop_indices.add(drop_idx)
                print(
                    f"  WITHIN-TURN DEDUP: dropping '{drop_name}' "
                    f"in favor of '{keep_name}'",
                    file=sys.stderr,
                )
                # If outer entity was dropped, stop comparing it
                if drop_idx == idx_a:
                    break

    return [e for i, e in enumerate(entities) if i not in drop_indices]


'''

new_lines = lines[:start] + [new_func] + lines[end:]
content = ''.join(new_lines)

# Verify syntax
import py_compile, tempfile, os
tmp = tempfile.NamedTemporaryFile(suffix='.py', delete=False, mode='w', encoding='utf-8')
tmp.write(content)
tmp.close()
try:
    py_compile.compile(tmp.name, doraise=True)
    print("Syntax check passed")
except py_compile.PyCompileError as e:
    print(f"SYNTAX ERROR: {e}")
    os.unlink(tmp.name)
    exit(1)
os.unlink(tmp.name)

open('tools/semantic_extraction.py', 'w', encoding='utf-8').write(content)
print("Done. File updated successfully.")
