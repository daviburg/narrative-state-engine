"""CLI entry point for CrewAI orchestration."""

import argparse
import os
import sys
import subprocess

# Ensure the repo root is on sys.path so `crew` is importable as a package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def cmd_extract(args):
    from crew.crews.extraction_crew import create_extraction_crew

    crew = create_extraction_crew(
        session=args.session,
        start_turn=args.start,
        end_turn=args.end,
        llm=args.llm,
    )
    result = crew.kickoff()
    print(result)


def cmd_optimize(args):
    from crew.crews.optimization_crew import create_optimization_crew

    crew = create_optimization_crew(
        target=args.target,
        model=args.model,
        llm=args.llm,
    )
    result = crew.kickoff()
    print(result)


def cmd_release(args):
    from crew.crews.release_crew import create_release_crew

    crew = create_release_crew(
        branch=args.branch,
        llm=args.llm,
    )
    result = crew.kickoff()
    print(result)


def cmd_vscode(args):
    from crewai import LLM

    from crew.crews.vscode_crew import create_vscode_crew
    from crew.tools.vscode_agent import (
        ensure_bridge_running,
        start_bridge_server,
    )

    # Build LLM object directly — avoids litellm provider matching
    # For arclight, use: --llm-base-url http://arclight:8000/v1
    llm = LLM(
        model=args.llm,
        base_url=args.llm_base_url,
        api_key="not-needed",
    )

    bridge_url = f"http://127.0.0.1:{args.port}"
    proc = None
    workspace = os.path.abspath(args.workspace)

    if not ensure_bridge_running(bridge_url):
        print(f"Starting bridge server on port {args.port}...")
        proc = start_bridge_server(workspace, port=args.port)
        print("Bridge server ready.")

    try:
        crew = create_vscode_crew(
            task_description=args.task,
            agent_name=args.agent,
            bridge_url=bridge_url,
            llm=llm,
        )
        result = crew.kickoff()
        print(result)
    finally:
        if proc is not None:
            import requests

            try:
                requests.post(f"{bridge_url}/session/close", timeout=10)
            except requests.RequestException:
                pass  # Best-effort session close; server is being terminated
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def main():
    parser = argparse.ArgumentParser(
        description="CrewAI orchestration for narrative-state-engine"
    )
    parser.add_argument(
        "--llm", default="ollama/qwen2.5:14b-8k",
        help="LLM identifier for agent reasoning (default: ollama/qwen2.5:14b-8k)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Extract command
    ext = subparsers.add_parser("extract", help="Run extraction crew")
    ext.add_argument("--session", required=True, help="Session directory name")
    ext.add_argument("--start", type=int, required=True, help="Start turn number")
    ext.add_argument("--end", type=int, required=True, help="End turn number")
    ext.set_defaults(func=cmd_extract)

    # Optimize command
    opt = subparsers.add_parser("optimize", help="Run optimization crew")
    opt.add_argument("--target", choices=["b70", "rtx4070"], required=True, help="GPU target")
    opt.add_argument("--model", default="qwen2.5:14b", help="Model to benchmark")
    opt.set_defaults(func=cmd_optimize)

    # Release command
    rel = subparsers.add_parser("release", help="Run release validation crew")
    rel.add_argument("--branch", required=True, help="Branch name to validate")
    rel.set_defaults(func=cmd_release)

    # VS Code agent command
    vsc = subparsers.add_parser("vscode", help="Delegate a task to VS Code Copilot agent")
    vsc.add_argument("--llm", default="qwen3.5-9b-q4_k_m",
        help="LLM model name (default: qwen3.5-9b-q4_k_m)")
    vsc.add_argument("--llm-base-url", default="http://localhost:8081/v1",
        help="Base URL for OpenAI-compatible LLM server (default: http://localhost:8081/v1)")
    vsc.add_argument("--task", required=True, help="Task description for the agent")
    vsc.add_argument("--agent", default="developer", help="VS Code agent mode (default: developer)")
    vsc.add_argument("--workspace", default=os.getcwd(), help="Workspace path (default: cwd)")
    vsc.add_argument("--port", type=int, default=7400, help="Bridge server port (default: 7400)")
    vsc.set_defaults(func=cmd_vscode)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
