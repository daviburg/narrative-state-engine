"""Tools for running and validating the extraction pipeline."""

import subprocess
from pathlib import Path

from crewai.tools import tool

REPO_ROOT = Path(__file__).resolve().parents[2]


@tool("Run extraction pipeline")
def run_extraction(session: str, start_turn: int, end_turn: int) -> str:
    """Run the extraction pipeline for a range of turns in a session.

    Args:
        session: Session directory name (e.g., 'test-validation')
        start_turn: First turn number to extract
        end_turn: Last turn number to extract
    """
    cmd = [
        "python", str(REPO_ROOT / "tools" / "bootstrap_session.py"),
        "--session", session,
        "--start-turn", str(start_turn),
        "--end-turn", str(end_turn),
        "--extract",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    return f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}\n\nReturn code: {result.returncode}"


@tool("Validate extraction against ground truth")
def validate_extraction(session: str) -> str:
    """Run extraction validation against ground truth fixtures.

    Args:
        session: Session directory name to validate
    """
    cmd = [
        "python", str(REPO_ROOT / "tools" / "validate_extraction.py"),
        "--session", session,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    return f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}\n\nReturn code: {result.returncode}"


@tool("Check JSON schema compliance")
def validate_schemas() -> str:
    """Run schema validation across all JSON files in the framework."""
    cmd = ["python", str(REPO_ROOT / "tools" / "validate.py")]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    return f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}\n\nReturn code: {result.returncode}"
