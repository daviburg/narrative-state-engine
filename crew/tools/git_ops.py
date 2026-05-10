"""Tools for git and GitHub operations."""

import subprocess
from pathlib import Path

from crewai.tools import tool

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_git(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        cwd=cwd or REPO_ROOT,
    )
    return f"{result.stdout}\n{result.stderr}".strip()


@tool("Get git diff for a branch")
def get_branch_diff(branch: str) -> str:
    """Get the diff between a branch and main.

    Args:
        branch: Branch name to diff against main
    """
    return _run_git(["diff", "main...", branch, "--stat"])


@tool("List open GitHub issues")
def list_open_issues(labels: str = "") -> str:
    """List open GitHub issues, optionally filtered by label.

    Args:
        labels: Comma-separated labels to filter by (e.g., 'bug,enhancement')
    """
    cmd = ["gh", "issue", "list", "--state", "open", "--limit", "30"]
    if labels:
        cmd.extend(["--label", labels])
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    return result.stdout or result.stderr


@tool("Get branch status")
def branch_status() -> str:
    """Show current branch, status, and recent commits."""
    branch = _run_git(["branch", "--show-current"])
    status = _run_git(["status", "--short"])
    log = _run_git(["log", "--oneline", "-10"])
    return f"Branch: {branch}\n\nStatus:\n{status}\n\nRecent commits:\n{log}"
