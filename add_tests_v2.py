"""Add _within_turn_dedup tests to test_dedup_fuzzy.py."""
import os

test_file = 'tests/test_dedup_fuzzy.py'
content = open(test_file, 'r', encoding='utf-8').read()

# 1. Update import if needed
old_import = 'from semantic_extraction import _dedup_catalogs'
new_import = 'from semantic_extraction import _dedup_catalogs, _within_turn_dedup'
if new_import not in content:
    assert old_import in content, "Import not found"
    content = content.replace(old_import, new_import, 1)
    print("Updated import")
else:
    print("Import already updated")

# 2. Add tests at end of file (only if not already there)
if 'test_within_turn_dedup_levenshtein_merge' in content:
    print("Tests already present, skipping")
else:
    # Read the test additions from a separate file
    additions_file = 'test_additions.txt'
    additions = open(additions_file, 'r', encoding='utf-8').read()
    content = content.rstrip() + '\n' + additions
    open(test_file, 'w', encoding='utf-8').write(content)
    print(f"Tests added. File now has {len(content.splitlines())} lines.")

print("Done.")
