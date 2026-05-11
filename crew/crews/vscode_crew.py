"""VS Code agent crew — delegates tasks to VS Code Copilot via HTTP bridge."""

from typing import Any

from crewai import Agent, Crew, Process, Task

from crew.tools.vscode_agent import VSCodeAgentTool


def create_vscode_crew(
    task_description: str,
    agent_name: str = "developer",
    bridge_url: str = "http://127.0.0.1:7400",
    llm: Any = None,
) -> Crew:
    """Create a crew that delegates work to a VS Code Copilot agent.

    Args:
        task_description: What the crew should accomplish.
        agent_name: VS Code agent mode to use (default: "developer").
        bridge_url: URL of the HTTP bridge server.
        llm: LLM identifier for agent reasoning.

    Returns:
        A Crew ready to kick off.
    """
    tool = VSCodeAgentTool(bridge_url=bridge_url, agent=agent_name)

    coder = Agent(
        role="VS Code Developer",
        goal=(
            "Complete the assigned task by delegating work to the VS Code Copilot "
            "agent, which has full workspace access including file editing, "
            "terminal commands, and all VS Code tools."
        ),
        backstory=(
            "You are a project manager who delegates coding tasks to a VS Code "
            "Copilot agent. You break down the task, send clear instructions, "
            "and verify the results."
        ),
        tools=[tool],
        llm=llm,
        verbose=True,
    )

    task = Task(
        description=task_description,
        expected_output="Summary of completed work and any issues encountered.",
        agent=coder,
    )

    return Crew(
        agents=[coder],
        tasks=[task],
        process=Process.sequential,
        verbose=True,
    )
