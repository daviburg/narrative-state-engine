"""Tests for the wait MCP server tool."""

import asyncio
import os
import sys
import time

import pytest

try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools", "mcp"))
    from wait_server import wait
except ImportError:
    pytest.skip("mcp package not installed", allow_module_level=True)


class TestWaitTool:
    """Test the wait tool function directly (without MCP transport)."""

    def test_short_wait(self):
        """A 1-second wait should complete in ~1 second."""
        start = time.monotonic()
        result = asyncio.run(wait(1))
        elapsed = time.monotonic() - start
        assert "Wait complete" in result
        assert elapsed >= 0.9
        assert elapsed < 2.0

    def test_wait_with_message(self):
        """Message should be included in the result."""
        result = asyncio.run(wait(1, message="extraction run"))
        assert "extraction run" in result

    def test_rejects_zero(self):
        """Zero seconds should be rejected."""
        result = asyncio.run(wait(0))
        assert "Error" in result

    def test_rejects_negative(self):
        """Negative seconds should be rejected."""
        result = asyncio.run(wait(-5))
        assert "Error" in result

    def test_rejects_over_max(self):
        """Over 14400 seconds should be rejected."""
        result = asyncio.run(wait(14401))
        assert "Error" in result
        assert "14400" in result

    def test_returns_elapsed_time(self):
        """Result should include actual elapsed time."""
        result = asyncio.run(wait(1))
        assert "actual elapsed" in result

    def test_rejects_bool(self):
        """Booleans should be rejected even though isinstance(True, int) is True."""
        result = asyncio.run(wait(True))
        assert "Error" in result
        result = asyncio.run(wait(False))
        assert "Error" in result

    def test_accepts_boundary_one(self):
        """Boundary value: wait(1) should succeed."""
        result = asyncio.run(wait(1))
        assert "Wait complete" in result

    def test_wait_cancelled(self):
        """CancelledError during sleep should return a cancellation message."""

        async def cancel_after(delay):
            task = asyncio.create_task(wait(60))
            await asyncio.sleep(delay)
            task.cancel()
            return await task

        result = asyncio.run(cancel_after(0.1))
        assert "cancelled" in result.lower()
        assert "60s" in result
