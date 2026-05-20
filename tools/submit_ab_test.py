#!/usr/bin/env python3
"""Submit an A/B extraction quality test to the orchestrator.

Generates a TaskDefinition JSON file for the ``ab_test`` adapter and
optionally submits it directly via the orchestrator CLI.

The orchestrator adapter runs bootstrap_session.py for each variant × run
combination, collects per-run entity counts, and flags anomalies (zero-entity
runs, >20 % inter-variant divergence) on the dashboard.

Usage
-----
    # Generate and immediately submit:
    python tools/submit_ab_test.py \\
        --pr 399 \\
        --variant-a main \\
        --variant-b feat/my-change \\
        --repo-a /path/to/repo-a \\
        --repo-b /path/to/repo-b \\
        --submit

    # Generate JSON only (inspect before submitting):
    python tools/submit_ab_test.py \\
        --pr 399 \\
        --variant-a main \\
        --variant-b feat/my-change \\
        --repo-a /path/to/repo-a \\
        --repo-b /path/to/repo-b \\
        --output /tmp/ab-test-pr399.json

Environment variables (set by the orchestrator; not needed for JSON generation):
    AB_TEST_PR, AB_TEST_VARIANT_A, AB_TEST_VARIANT_B, AB_TEST_RUNS,
    AB_TEST_TURNS, AB_TEST_BASE_URL_A, AB_TEST_BASE_URL_B,
    AB_TEST_REPO_A, AB_TEST_REPO_B, AB_TEST_SESSION, AB_TEST_TRANSCRIPT,
    AB_TEST_OUTPUT_DIR
"""

import argparse
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path


_TASK_ID_PATTERN = re.compile(r'^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$')


def _validate_branch_name(name: str) -> bool:
    """Validate branch name using git check-ref-format (authoritative).

    Falls back to a permissive regex if git is not available.
    """
    try:
        result = subprocess.run(
            ["git", "check-ref-format", "--branch", name],
            capture_output=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        # git not available — fall back to permissive pattern
        return bool(re.match(r'^[^\x00-\x1f ~^:?*\[\\]+$', name))


def _make_task_id(pr: int, suffix: str | None = None) -> str:
    """Generate a valid task ID for the orchestrator."""
    short = uuid.uuid4().hex[:6]
    base = f"ab-test-pr{pr}-{short}"
    if suffix:
        safe = re.sub(r'[^a-z0-9-]', '-', suffix.lower())[:20].strip('-')
        base = f"ab-test-pr{pr}-{safe}-{short}"
    return base[:64].rstrip('-')


def build_task(args: argparse.Namespace) -> dict:
    """Build a TaskDefinition dict from parsed arguments."""
    task_id = args.task_id or _make_task_id(args.pr)
    if not _TASK_ID_PATTERN.match(task_id):
        raise ValueError(
            f"Invalid task ID '{task_id}': must match ^[a-z0-9][a-z0-9-]{{0,62}}[a-z0-9]$"
        )

    for field, value in [
        ("variant_a", args.variant_a),
        ("variant_b", args.variant_b),
    ]:
        if value and not _validate_branch_name(value):
            raise ValueError(f"Invalid branch name for {field}: {value!r}")

    metadata: dict = {
        "pr_number": args.pr,
        "variant_a_branch": args.variant_a,
        "variant_b_branch": args.variant_b,
    }
    if args.runs is not None:
        metadata["runs_per_variant"] = args.runs
    if args.turns is not None:
        metadata["turns"] = args.turns
    if args.repo_a:
        metadata["repo_a"] = str(Path(args.repo_a).resolve())
    if args.repo_b:
        metadata["repo_b"] = str(Path(args.repo_b).resolve())
    if args.base_url_a:
        metadata["base_url_a"] = args.base_url_a
    if args.base_url_b:
        metadata["base_url_b"] = args.base_url_b
    if args.session:
        metadata["session"] = args.session
    if args.transcript:
        metadata["transcript"] = args.transcript
    if args.output_dir:
        metadata["output_dir"] = args.output_dir

    task = {
        "id": task_id,
        "name": f"A/B test PR #{args.pr}: {args.variant_a} vs {args.variant_b}",
        "adapter": "ab_test",
        "timeout": args.timeout,
        "metadata": metadata,
        "success_criteria": [
            {"type": "exit_code", "expected": 0},
            {"type": "entity_count", "expected": 1},
            {"type": "variant_divergence", "expected": args.divergence_threshold},
        ],
    }
    return task


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Submit an A/B extraction quality test to the orchestrator.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Required
    parser.add_argument("--pr", type=int, required=True, help="PR number being tested")
    parser.add_argument("--variant-a", required=True, help="Branch name for variant A (baseline)")
    parser.add_argument("--variant-b", required=True, help="Branch name for variant B (candidate)")

    # Repo paths
    parser.add_argument("--repo-a", help="Filesystem path to the variant A repo checkout")
    parser.add_argument("--repo-b", help="Filesystem path to the variant B repo checkout")

    # Optional extraction config
    parser.add_argument("--runs", type=int, default=None,
                        help="Runs per variant (default: orchestrator config, typically 3)")
    parser.add_argument("--turns", default=None, help="Turn range, e.g. '1-30' or '30'")
    parser.add_argument("--base-url-a", default="http://localhost:8080/v1",
                        help="LLM endpoint URL for variant A (default: http://localhost:8080/v1)")
    parser.add_argument("--base-url-b", default="http://localhost:8081/v1",
                        help="LLM endpoint URL for variant B (default: http://localhost:8081/v1)")
    parser.add_argument("--session", default=None, help="Session path (e.g. sessions/session-import)")
    parser.add_argument("--transcript", default=None, help="Path to transcript file")
    parser.add_argument("--output-dir", default=None, help="Directory for per-run output files")

    # Task config
    parser.add_argument("--task-id", default=None, help="Override auto-generated task ID")
    parser.add_argument("--timeout", type=int, default=7200,
                        help="Task timeout in seconds (default: 7200)")
    parser.add_argument("--divergence-threshold", type=float, default=20.0,
                        help="Max allowed inter-variant divergence %% before flagging (default: 20.0)")

    # Output / submission
    parser.add_argument("--output", "-o", default=None,
                        help="Write task JSON to this file (default: ab-test-pr<N>.json)")
    parser.add_argument("--submit", action="store_true",
                        help="Submit immediately via 'python -m saas.orchestrator submit'")
    parser.add_argument("--orchestrator-config", default=None,
                        help="Path to orchestrator config.json (only used with --submit)")

    args = parser.parse_args(argv)

    if args.runs is not None and args.runs < 1:
        parser.error("--runs must be >= 1")

    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        task = build_task(args)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    task_json = json.dumps(task, indent=2)

    out_path = Path(args.output) if args.output else Path(f"ab-test-pr{args.pr}.json")
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(task_json + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"Error writing {out_path}: {exc}", file=sys.stderr)
        return 1
    print(f"Task definition written to: {out_path}", file=sys.stderr)
    print(task_json)

    if args.submit:
        cmd = [sys.executable, "-m", "saas.orchestrator", "submit", str(out_path)]
        if args.orchestrator_config:
            cmd += ["--config", args.orchestrator_config]
        print(f"\nSubmitting: {' '.join(cmd)}", file=sys.stderr)
        result = subprocess.run(cmd)
        return result.returncode

    print(
        f"\nTo submit:\n  python -m saas.orchestrator submit {out_path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
