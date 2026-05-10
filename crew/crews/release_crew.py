"""Release crew — test, review, and prepare for merge."""

from crewai import Agent, Crew, Process, Task

from crew.tools.git_ops import branch_status, get_branch_diff, list_open_issues
from crew.tools.testing import run_tests, test_summary


def create_release_crew(
    branch: str,
    llm: str = "ollama/qwen2.5:14b-8k",
) -> Crew:
    """Create a crew for validating a branch before merge.

    Args:
        branch: Branch name to validate
        llm: LLM for the agents to use for reasoning
    """

    tester = Agent(
        role="QA Engineer",
        goal=f"Verify that branch '{branch}' passes all tests with no regressions",
        backstory=(
            "You run the full test suite and compare results against the expected "
            "baseline. You identify any new failures and determine if they're "
            "caused by the branch's changes."
        ),
        tools=[run_tests, test_summary],
        llm=llm,
        verbose=True,
    )

    reviewer = Agent(
        role="Code Reviewer",
        goal=f"Review changes on '{branch}' for quality and standards compliance",
        backstory=(
            "You review code changes against the project's standards: conventional "
            "commits, schema compliance, documentation updates (Rule 8), provenance "
            "tracking, and test coverage."
        ),
        tools=[get_branch_diff, branch_status, list_open_issues],
        llm=llm,
        verbose=True,
    )

    test_task = Task(
        description=(
            f"Check out branch '{branch}' and run the full test suite. "
            "Report pass/fail counts and identify any new failures vs main."
        ),
        expected_output="Test report: total, passed, failed, new failures list.",
        agent=tester,
    )

    review_task = Task(
        description=(
            f"Review the diff for branch '{branch}' against main. Check: "
            "correctness, schema compliance, documentation updates, test coverage, "
            "commit message format, and security."
        ),
        expected_output=(
            "Review verdict (approve/request-changes) with findings organized "
            "by severity: blocking, suggestion, nit."
        ),
        agent=reviewer,
    )

    return Crew(
        agents=[tester, reviewer],
        tasks=[test_task, review_task],
        process=Process.sequential,
        verbose=True,
    )
