"""Extraction run crew — orchestrates extraction with validation."""

from crewai import Agent, Crew, Process, Task

from crew.tools.extraction import run_extraction, validate_extraction, validate_schemas
from crew.tools.testing import run_tests, test_summary


def create_extraction_crew(
    session: str,
    start_turn: int,
    end_turn: int,
    llm: str = "ollama/qwen2.5:14b-8k",
) -> Crew:
    """Create a crew for running and validating extraction."""

    extractor = Agent(
        role="Extraction Pipeline Operator",
        goal=f"Run extraction for session '{session}' turns {start_turn}-{end_turn} and ensure quality",
        backstory=(
            "You operate the semantic extraction pipeline. You run extraction in "
            "incremental batches, monitor for errors, and stop if quality degrades."
        ),
        tools=[run_extraction, validate_extraction],
        llm=llm,
        verbose=True,
    )

    validator = Agent(
        role="Quality Validator",
        goal="Validate extraction output meets quality thresholds",
        backstory=(
            "You validate extraction results against ground truth, run schema checks, "
            "and ensure no regressions were introduced."
        ),
        tools=[validate_extraction, validate_schemas, run_tests, test_summary],
        llm=llm,
        verbose=True,
    )

    extract_task = Task(
        description=(
            f"Run extraction for session '{session}', turns {start_turn} to {end_turn}. "
            "Monitor the output for errors. If extraction fails on a turn, note it and continue."
        ),
        expected_output="Extraction log: turns processed, entities found, any errors.",
        agent=extractor,
    )

    validate_task = Task(
        description=(
            f"After extraction completes, validate the results for session '{session}'. "
            "Run ground truth comparison, schema validation, and the test suite. "
            "Report quality metrics and any regressions."
        ),
        expected_output=(
            "Quality report: entity coverage %, schema compliance, test pass rate, "
            "and list of any regressions or new issues."
        ),
        agent=validator,
    )

    return Crew(
        agents=[extractor, validator],
        tasks=[extract_task, validate_task],
        process=Process.sequential,
        verbose=True,
    )
