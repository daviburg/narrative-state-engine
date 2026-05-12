#!/usr/bin/env python3
"""
wait_server.py — MCP server providing a wait/sleep tool for coordinator scheduling.

Allows the coordinator agent to pause for a specified duration before resuming
work, enabling token-efficient monitoring of long-running processes.
"""

import asyncio
import time

from mcp.server.fastmcp import FastMCP

server = FastMCP("wait-server")

MAX_WAIT_SECONDS = 14400  # 4 hours


@server.tool()
async def wait(seconds: int, message: str = "") -> str:
    """Wait for the specified number of seconds before returning.

    Use this to pause between checks on long-running processes (extraction runs,
    benchmarks) instead of repeatedly dispatching subagents.

    Args:
        seconds: Duration to wait (1-14400, max 4 hours)
        message: Optional description of what you're waiting for

    Returns:
        Confirmation with actual elapsed time
    """
    if not isinstance(seconds, int) or seconds < 1:
        return f"Error: seconds must be a positive integer, got {seconds}"
    if seconds > MAX_WAIT_SECONDS:
        return (
            f"Error: maximum wait is {MAX_WAIT_SECONDS} seconds (4 hours),"
            f" got {seconds}"
        )

    start = time.monotonic()
    if message:
        print(f"[wait-server] Waiting {seconds}s: {message}")

    await asyncio.sleep(seconds)

    elapsed = time.monotonic() - start
    result = f"Wait complete. Requested: {seconds}s, actual elapsed: {elapsed:.1f}s"
    if message:
        result += f" (was waiting for: {message})"
    return result


if __name__ == "__main__":
    server.run()
