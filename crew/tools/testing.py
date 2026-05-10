"""Tools for running tests and quality checks."""

import subprocess
from pathlib import Path

from crewai.tools import tool

REPO_ROOT = Path(__file__).resolve().parents[2]


@tool("Run pytest suite")
def run_tests(test_pattern: str = "") -> str:
    """Run the pytest test suite, optionally filtered by pattern.

    Args:
        test_pattern: Optional test file or pattern (e.g., 'test_birth_entities')
    """
    cmd = ["python", "-m", "pytest", "tests/", "-v", "--tb=short"]
    if test_pattern:
        cmd.extend(["-k", test_pattern])
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    # Truncate if too long
    output = result.stdout[-4000:] if len(result.stdout) > 4000 else result.stdout
    return f"{output}\n\nSTDERR:\n{result.stderr[-1000:]}\n\nReturn code: {result.returncode}"


@tool("Get test count summary")
def test_summary() -> str:
    """Run pytest in summary-only mode to get pass/fail counts."""
    cmd = ["python", "-m", "pytest", "tests/", "--tb=no", "-q"]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    return result.stdout + result.stderr
