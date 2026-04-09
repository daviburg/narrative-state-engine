#!/usr/bin/env python3
"""
validate.py — Validate JSON files against their schemas.

Usage:
    python tools/validate.py --session sessions/session-001
    python tools/validate.py --framework framework
    python tools/validate.py --all
    python tools/validate.py --file sessions/session-001/derived/state.json --schema schemas/state.schema.json
    python tools/validate.py --session sessions/session-001 --strict
"""

import argparse
import glob
import json
import os
import re
import sys

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

JSONSCHEMA_REQUIRED_MSG = (
    "ERROR: jsonschema is not installed. Full schema validation requires it.\n"
    "Install it with:  pip install -r requirements.txt\n"
    "To run syntax-only checks instead, use:  --syntax-only"
)


# Map of JSON file basename patterns to schema files
SCHEMA_MAP = {
    "state.json": "schemas/state.schema.json",
    "objectives.json": "schemas/objective.schema.json",
    "evidence.json": "schemas/evidence.schema.json",
    "prompt-candidates.json": "schemas/prompt-candidate.schema.json",
    "characters.json": "schemas/entity.schema.json",
    "locations.json": "schemas/entity.schema.json",
    "factions.json": "schemas/entity.schema.json",
    "items.json": "schemas/entity.schema.json",
    "events.json": "schemas/event.schema.json",
    "anomalies.json": "schemas/anomaly.schema.json",
    "plot-threads.json": "schemas/plot-thread.schema.json",
    "dm-profile.json": "schemas/dm-profile.schema.json",
    "session-events.json": "schemas/session-events.schema.json",
    "timeline.json": "schemas/timeline.schema.json",
    "season-summaries.json": "schemas/season-summary.schema.json",
}


def load_schema(schema_path: str) -> dict:
    with open(schema_path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_file(json_path: str, schema_path: str, syntax_only: bool = False) -> list[str]:
    """Validate a JSON file against a schema. Returns a list of error messages."""
    errors = []

    # Basic JSON parse check
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return [f"JSON parse error: {e}"]

    if syntax_only:
        return []  # Caller explicitly requested syntax-only checks

    try:
        schema = load_schema(schema_path)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        return [f"Schema load error: {e}"]

    try:
        validator = jsonschema.Draft7Validator(schema)
        # Files can be arrays (catalogs) or objects; validate each item in arrays
        if isinstance(data, list):
            for i, item in enumerate(data):
                for error in validator.iter_errors(item):
                    errors.append(f"[{i}] {error.message} (path: {list(error.path)})")
        else:
            for error in validator.iter_errors(data):
                errors.append(f"{error.message} (path: {list(error.path)})")
    except Exception as e:
        errors.append(f"Validation error: {e}")

    return errors


# ---------------------------------------------------------------------------
# Strict-mode completeness checks
# ---------------------------------------------------------------------------

_TODO_PATTERN = re.compile(r"\bTODO\b", re.IGNORECASE)


def _count_dm_turns(session_dir: str) -> int:
    """Count DM turn files in a session's transcript/ directory."""
    transcript_dir = os.path.join(session_dir, "transcript")
    if not os.path.isdir(transcript_dir):
        return 0
    return len(glob.glob(os.path.join(transcript_dir, "turn-*-dm.md")))


def _find_todo_strings(data, path: str = "") -> list[str]:
    """Recursively find string values containing TODO placeholders."""
    warnings = []
    if isinstance(data, dict):
        for key, value in data.items():
            warnings.extend(_find_todo_strings(value, f"{path}.{key}"))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            warnings.extend(_find_todo_strings(item, f"{path}[{i}]"))
    elif isinstance(data, str) and _TODO_PATTERN.search(data):
        warnings.append(f"TODO placeholder at {path}: {data!r}")
    return warnings


def check_completeness(json_path: str, session_dir: str | None) -> list[str]:
    """
    Check a derived file for extraction completeness.

    Returns a list of warning strings. Empty list means the file looks
    adequately populated.
    """
    warnings: list[str] = []
    fname = os.path.basename(json_path)

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []  # Schema validation already catches parse errors

    # 1. Check for TODO placeholder values in any derived file
    todo_hits = _find_todo_strings(data)
    warnings.extend(todo_hits)

    # 2. Determine DM turn count for coverage checks
    dm_turns = _count_dm_turns(session_dir) if session_dir else 0

    # 3. File-specific completeness checks
    if fname == "evidence.json":
        if isinstance(data, list):
            if len(data) == 0 and dm_turns > 0:
                warnings.append(
                    f"evidence.json is empty but session has {dm_turns} DM turn(s) — "
                    "expected at least some extracted evidence"
                )
            elif dm_turns > 0:
                coverage = len(data) / dm_turns
                warnings.append(
                    f"Evidence coverage: {len(data)} entries from {dm_turns} DM turns "
                    f"({coverage:.0%})"
                )

    elif fname == "objectives.json":
        if isinstance(data, list) and len(data) == 0 and dm_turns > 0:
            warnings.append(
                f"objectives.json is empty but session has {dm_turns} DM turn(s) — "
                "expected at least one objective after extraction"
            )

    elif fname == "state.json":
        if isinstance(data, dict):
            # Check for scaffold defaults
            ps = data.get("player_state", {})
            scaffold_values = {"Unknown", "Not established", "No NPCs contacted yet"}
            for field in ("location", "condition", "inventory_notes", "relationships_summary"):
                val = ps.get(field, "")
                if val in scaffold_values and dm_turns > 0:
                    warnings.append(
                        f"state.json player_state.{field} is still scaffold default "
                        f"({val!r}) after {dm_turns} DM turn(s)"
                    )

            # Check for empty constraint/opportunity arrays
            for array_field in ("known_constraints", "active_threads"):
                arr = data.get(array_field, [])
                if isinstance(arr, list) and len(arr) == 0 and dm_turns > 0:
                    warnings.append(
                        f"state.json {array_field} is empty after {dm_turns} DM turn(s)"
                    )

    return warnings


def _find_session_dir(json_path: str) -> str | None:
    """Given a path inside a session, walk up to find the session directory."""
    current = os.path.dirname(os.path.abspath(json_path))
    for _ in range(5):  # Don't walk too far
        if os.path.isdir(os.path.join(current, "transcript")) or \
           os.path.isdir(os.path.join(current, "derived")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


def validate_dir(directory: str, repo_root: str, syntax_only: bool = False,
                  strict: bool = False) -> tuple[int, int, int]:
    """Walk a directory and validate all JSON files with known schema mappings.

    Returns (passed, failed, completeness_warnings).
    """
    passed = 0
    failed = 0
    completeness_warnings = 0

    for root, _dirs, files in os.walk(directory):
        for fname in sorted(files):
            if not fname.endswith(".json"):
                continue
            json_path = os.path.join(root, fname)

            # Skip schema files themselves
            if os.path.abspath(json_path).startswith(
                os.path.abspath(os.path.join(repo_root, "schemas"))
            ):
                continue

            schema_rel = SCHEMA_MAP.get(fname)
            if not schema_rel:
                print(f"  [SKIP]   {json_path} (no schema mapping)")
                continue

            schema_path = os.path.join(repo_root, schema_rel)
            if not os.path.exists(schema_path):
                print(f"  [SKIP]   {json_path} (schema not found: {schema_path})")
                continue

            errors = validate_file(json_path, schema_path, syntax_only=syntax_only)
            if errors:
                print(f"  [FAIL]   {json_path}")
                for err in errors:
                    print(f"           {err}")
                failed += 1
            else:
                print(f"  [PASS]   {json_path}")
                passed += 1

            # Strict-mode completeness checks on derived files
            if strict and "derived" in os.path.normpath(json_path).split(os.sep):
                session_dir = _find_session_dir(json_path)
                warnings = check_completeness(json_path, session_dir)
                for w in warnings:
                    print(f"  [WARN]   {json_path}: {w}")
                completeness_warnings += len(warnings)

    return passed, failed, completeness_warnings


def find_repo_root() -> str:
    """Find the repository root by walking up from the tools directory."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # tools/ is one level below repo root
    return os.path.dirname(script_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate JSON files against schemas.")
    parser.add_argument("--session", help="Validate a specific session directory.")
    parser.add_argument("--framework", help="Validate the framework directory.")
    parser.add_argument("--all", action="store_true", help="Validate all known directories.")
    parser.add_argument("--file", help="Validate a specific JSON file.")
    parser.add_argument("--schema", help="Schema to use when validating --file.")
    parser.add_argument(
        "--syntax-only",
        action="store_true",
        help="Check JSON syntax only; skip schema validation. Use when jsonschema is not installed.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Enable completeness checks: warn on TODO placeholders, empty arrays, "
             "and low extraction coverage in derived files.",
    )
    args = parser.parse_args()

    if not any([args.session, args.framework, args.all, args.file]):
        parser.print_help()
        sys.exit(0)

    syntax_only = args.syntax_only
    if not HAS_JSONSCHEMA and not syntax_only:
        print(JSONSCHEMA_REQUIRED_MSG, file=sys.stderr)
        sys.exit(1)

    repo_root = find_repo_root()
    total_passed = 0
    total_failed = 0
    total_warnings = 0

    if args.file:
        if not args.schema:
            # Try to infer schema from filename
            fname = os.path.basename(args.file)
            schema_rel = SCHEMA_MAP.get(fname)
            if not schema_rel:
                print(f"ERROR: Cannot infer schema for '{fname}'. Use --schema.", file=sys.stderr)
                sys.exit(1)
            schema_path = os.path.join(repo_root, schema_rel)
        else:
            schema_path = args.schema

        errors = validate_file(args.file, schema_path, syntax_only=syntax_only)
        if errors:
            print(f"[FAIL] {args.file}")
            for err in errors:
                print(f"       {err}")
            sys.exit(1)
        else:
            print(f"[PASS] {args.file}")

        if args.strict and "derived" in os.path.normpath(args.file).split(os.sep):
            session_dir = _find_session_dir(args.file)
            warnings = check_completeness(args.file, session_dir)
            for w in warnings:
                print(f"[WARN] {args.file}: {w}")
            if warnings:
                print(f"\nCompleteness: {len(warnings)} warning(s)")
                sys.exit(2)
        return

    directories = []
    if args.session:
        directories.append(args.session)
    if args.framework:
        directories.append(args.framework)
    if args.all:
        for d in ["sessions", "framework"]:
            full = os.path.join(repo_root, d)
            if os.path.isdir(full):
                directories.append(full)

    for directory in directories:
        print(f"\nValidating: {directory}")
        p, f, w = validate_dir(directory, repo_root, syntax_only=syntax_only,
                               strict=args.strict)
        total_passed += p
        total_failed += f
        total_warnings += w

    print(f"\nValidated {total_passed + total_failed} files: "
          f"{total_passed} passed, {total_failed} failed")
    if args.strict:
        print(f"Completeness: {total_warnings} warning(s)")
    if total_failed > 0:
        sys.exit(1)
    if args.strict and total_warnings > 0:
        sys.exit(2)


if __name__ == "__main__":
    main()
