"""VS Code Copilot agent bridge tools for CrewAI."""

import os
import subprocess
import time

import requests
from crewai.tools import BaseTool
from pydantic import Field

BRIDGE_DIR = os.path.join(os.path.dirname(__file__), "..", "bridge")


def ensure_bridge_running(url: str = "http://127.0.0.1:7400") -> bool:
    """Check if the bridge server is running by hitting /health."""
    try:
        resp = requests.get(f"{url}/health", timeout=5)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def start_bridge_server(workspace_path: str, port: int = 7400) -> subprocess.Popen:
    """Launch the HTTP bridge server and wait for it to be ready.

    Args:
        workspace_path: Absolute path to the workspace for VS Code.
        port: Port for the bridge server (default 7400).

    Returns:
        The subprocess handle for the running server.

    Raises:
        RuntimeError: If the server doesn't become healthy within 30 seconds.
    """
    url = f"http://127.0.0.1:{port}"

    if ensure_bridge_running(url):
        raise RuntimeError(
            f"Port {port} is already in use. Stop the existing server first."
        )

    bridge_abs = os.path.abspath(BRIDGE_DIR)
    server_js = os.path.join(bridge_abs, "dist", "server.js")
    if not os.path.isfile(server_js):
        raise RuntimeError(
            f"Bridge server not built: {server_js} not found. "
            f"Run 'npm install && npm run build' in {bridge_abs}"
        )

    proc = subprocess.Popen(
        ["node", "dist/server.js", "--port", str(port)],
        cwd=bridge_abs,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"Bridge server exited with code {proc.returncode}"
            )
        if ensure_bridge_running(url):
            # Start a session with the workspace path
            try:
                requests.post(
                    f"{url}/session/start",
                    json={
                        "workspacePath": workspace_path,
                        "defaultTimeout": 300000,
                    },
                    timeout=30,
                )
            except requests.RequestException as exc:
                proc.terminate()
                raise RuntimeError(
                    f"Bridge started but session/start failed: {exc}"
                ) from exc
            return proc
        time.sleep(1)

    proc.terminate()
    raise RuntimeError("Bridge server did not become healthy within 30 seconds")


class VSCodeAgentTool(BaseTool):
    """Send a prompt to a VS Code Copilot agent and get the response.

    The agent has full access to the workspace, can read/write files,
    run terminal commands, and use all VS Code tools.
    """

    name: str = "vscode_agent"
    description: str = (
        "Send a prompt to a VS Code Copilot agent and get the response. "
        "The agent has full access to the workspace, can read/write files, "
        "run terminal commands, and use all VS Code tools."
    )
    bridge_url: str = Field(default="http://127.0.0.1:7400")
    agent: str = Field(default="developer")

    def _run(self, prompt: str) -> str:
        try:
            resp = requests.post(
                f"{self.bridge_url}/chat/send",
                json={"agent": self.agent, "prompt": prompt},
                timeout=300,
            )
            resp.raise_for_status()
            return resp.json().get("response", "")
        except requests.ConnectionError:
            return "Error: Bridge server is not running. Start it with start_bridge_server()."
        except requests.Timeout:
            return "Error: Request timed out after 300 seconds."
        except requests.HTTPError as exc:
            return f"Error: HTTP {exc.response.status_code} — {exc.response.text}"
        except requests.RequestException as exc:
            return f"Error: {exc}"


class VSCodeNewChatTool(BaseTool):
    """Start a new chat session in VS Code, clearing previous context."""

    name: str = "vscode_new_chat"
    description: str = "Start a new chat session in VS Code, clearing previous context."
    bridge_url: str = Field(default="http://127.0.0.1:7400")

    def _run(self, **kwargs) -> str:
        try:
            resp = requests.post(f"{self.bridge_url}/chat/new", timeout=30)
            resp.raise_for_status()
            return "New chat session started."
        except requests.RequestException as exc:
            return f"Error: {exc}"


class VSCodeMetricsTool(BaseTool):
    """Get usage metrics for the current VS Code chat session."""

    name: str = "vscode_metrics"
    description: str = (
        "Get usage metrics for the current VS Code chat session: "
        "turn count, characters sent/received, estimated tokens."
    )
    bridge_url: str = Field(default="http://127.0.0.1:7400")

    def _run(self, **kwargs) -> str:
        try:
            resp = requests.get(f"{self.bridge_url}/chat/metrics", timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return (
                f"Turns: {data.get('turnCount', 0)}, "
                f"Sent: {data.get('totalCharsSent', 0)} chars "
                f"(~{data.get('estimatedTokensSent', 0)} tokens), "
                f"Received: {data.get('totalCharsReceived', 0)} chars "
                f"(~{data.get('estimatedTokensReceived', 0)} tokens)"
            )
        except requests.RequestException as exc:
            return f"Error: {exc}"


class VSCodeSessionCloseTool(BaseTool):
    """Close the VS Code chat session and clean up resources."""

    name: str = "vscode_session_close"
    description: str = "Close the VS Code chat session and clean up resources."
    bridge_url: str = Field(default="http://127.0.0.1:7400")

    def _run(self, **kwargs) -> str:
        try:
            resp = requests.post(f"{self.bridge_url}/session/close", timeout=30)
            resp.raise_for_status()
            return "Session closed."
        except requests.RequestException as exc:
            return f"Error: {exc}"
