"""Tests for ov_serve.py keepalive and sequential request handling (#316).

Verifies that two sequential POST requests to /v1/chat/completions succeed
without the TCP connection being dropped between them.

Requires: fastapi, uvicorn, httpx, pytest-asyncio (server extras, not in
core requirements.txt).  The module is skipped in CI where these are absent.
"""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Skip the entire module when server-side dependencies are missing (CI).
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("uvicorn")
pytest_asyncio = pytest.importorskip("pytest_asyncio")

# ---------------------------------------------------------------------------
# Mock openvino_genai before importing ov_serve — save/restore to avoid
# leaking the mock into other test modules.
# ---------------------------------------------------------------------------
_orig_ov_module = sys.modules.get("openvino_genai")
_mock_ov = MagicMock()
sys.modules["openvino_genai"] = _mock_ov

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

import ov_serve  # noqa: E402


def teardown_module():
    """Restore original openvino_genai module entry after all tests run."""
    if _orig_ov_module is not None:
        sys.modules["openvino_genai"] = _orig_ov_module
    else:
        sys.modules.pop("openvino_genai", None)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeTokenizer:
    """Minimal tokenizer mock for ov_serve."""

    def encode(self, text):
        tokens = text.split()
        return SimpleNamespace(input_ids=tokens)

    def apply_chat_template(self, messages, add_generation_prompt=True, **kwargs):
        return " ".join(m["content"] for m in messages)

    def get_eos_token_id(self):
        return 0


class FakePipeline:
    """Minimal pipeline mock: returns canned text for each prompt."""

    def generate(self, prompts, gen_configs):
        results = []
        for _ in prompts:
            result = SimpleNamespace(m_generation_ids=["Hello from the mock pipeline."])
            results.append(result)
        return results

    def get_tokenizer(self):
        return FakeTokenizer()


@pytest.fixture()
def _patch_globals():
    """Inject fake pipeline/tokenizer into ov_serve module globals."""
    fake_pipeline = FakePipeline()
    fake_tokenizer = FakeTokenizer()

    orig_pipeline = ov_serve.pipeline
    orig_tokenizer = ov_serve.tokenizer
    orig_model_name = ov_serve.MODEL_NAME

    ov_serve.pipeline = fake_pipeline
    ov_serve.tokenizer = fake_tokenizer
    ov_serve.MODEL_NAME = "test-model"

    yield

    ov_serve.pipeline = orig_pipeline
    ov_serve.tokenizer = orig_tokenizer
    ov_serve.MODEL_NAME = orig_model_name


@pytest_asyncio.fixture()
async def async_client(_patch_globals):
    """Create an httpx AsyncClient bound to the FastAPI app with a running batch worker."""
    # We can't use the lifespan (it tries to load the real model),
    # so start the batch worker manually.
    # Note: httpx.ASGITransport does not trigger ASGI lifespan events (it only
    # handles HTTP scope), so the app's lifespan callback won't run here.
    import httpx

    ov_serve.batch_queue = asyncio.Queue()
    task = asyncio.create_task(ov_serve.batch_worker())

    transport = httpx.ASGITransport(app=ov_serve.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass  # Expected: we just cancelled the batch worker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chat_payload(content="Say hello"):
    return {
        "model": "test-model",
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 64,
        "temperature": 0.0,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_endpoint(_patch_globals):
    """Sanity check: /health returns 200."""
    import httpx

    ov_serve.batch_queue = asyncio.Queue()
    task = asyncio.create_task(ov_serve.batch_worker())

    transport = httpx.ASGITransport(app=ov_serve.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        resp = await client.get("/health")

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass  # Expected: we just cancelled the batch worker

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["model"] == "test-model"


@pytest.mark.asyncio
async def test_sequential_requests_succeed(async_client):
    """Two sequential POST requests must both return 200 (#316)."""
    resp1 = await async_client.post("/v1/chat/completions", json=_chat_payload("First"))
    assert resp1.status_code == 200, f"First request failed: {resp1.text}"

    resp2 = await async_client.post("/v1/chat/completions", json=_chat_payload("Second"))
    assert resp2.status_code == 200, f"Second request failed: {resp2.text}"

    # Both should return valid completions
    data1 = resp1.json()
    data2 = resp2.json()
    assert data1["choices"][0]["message"]["content"]
    assert data2["choices"][0]["message"]["content"]
    # Response IDs should be unique
    assert data1["id"] != data2["id"]


@pytest.mark.asyncio
async def test_three_sequential_requests(async_client):
    """Three sequential requests simulate a multi-turn extraction run."""
    for i in range(3):
        resp = await async_client.post(
            "/v1/chat/completions", json=_chat_payload(f"Turn {i}")
        )
        assert resp.status_code == 200, f"Request {i} failed: {resp.text}"


@pytest.mark.asyncio
async def test_timeout_keep_alive_default():
    """Verify the default TIMEOUT_KEEP_ALIVE is set to 120 seconds."""
    assert ov_serve.TIMEOUT_KEEP_ALIVE == 120


@pytest.mark.asyncio
async def test_cli_timeout_keep_alive_forwarded_to_uvicorn():
    """Verify --timeout-keep-alive is parsed and forwarded to uvicorn.run()."""
    # Save globals that main() mutates so other tests are not affected.
    saved = {
        attr: getattr(ov_serve, attr)
        for attr in (
            "MODEL_DIR", "CACHE_DIR", "MODEL_NAME", "BATCH_WAIT_MS",
            "MAX_BATCH_SIZE", "CACHE_SIZE_GB", "REQUEST_TIMEOUT_S",
            "TIMEOUT_KEEP_ALIVE", "EXTRA_STOP_TOKEN_IDS",
        )
    }
    try:
        with patch.object(ov_serve, "uvicorn") as mock_uvicorn:
            ov_serve.main([
                "--model-dir", "/tmp/fake-model",
                "--timeout-keep-alive", "300",
            ])
            mock_uvicorn.run.assert_called_once()
            call_kwargs = mock_uvicorn.run.call_args
            assert call_kwargs.kwargs.get("timeout_keep_alive") == 300 or \
                call_kwargs[1].get("timeout_keep_alive") == 300
    finally:
        for attr, val in saved.items():
            setattr(ov_serve, attr, val)


@pytest.mark.asyncio
async def test_admin_flush_drains_queue(async_client):
    """POST /admin/flush drains queued requests with 503 (#361)."""
    # Queue is empty initially
    resp = await async_client.post("/admin/flush")
    assert resp.status_code == 200
    data = resp.json()
    assert data["flushed"] == 0
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_admin_flush_clears_pending_requests(_patch_globals):
    """Flush cancels queued requests that haven't started processing (#361)."""
    import httpx

    ov_serve.batch_queue = asyncio.Queue()
    # Do NOT start batch_worker — requests will stay queued

    loop = asyncio.get_running_loop()

    # Queue some fake requests
    futures = []
    for i in range(3):
        future = loop.create_future()
        futures.append(future)
        req = ov_serve.BatchRequest(
            prompt=f"test prompt {i}",
            gen_config=None,
            future=future,
        )
        await ov_serve.batch_queue.put(req)

    assert ov_serve.batch_queue.qsize() == 3

    # Call flush via the endpoint
    transport = httpx.ASGITransport(app=ov_serve.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post("/admin/flush")

    assert resp.status_code == 200
    data = resp.json()
    assert data["flushed"] == 3
    assert data["status"] == "ok"
    assert ov_serve.batch_queue.qsize() == 0

    # All futures should have exceptions
    for f in futures:
        assert f.done()
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            f.result()
        assert exc_info.value.status_code == 503
